"""One completed turn, all the way through the write path.

There is exactly one function, and every caller — the RQ job, the probe
management command, a future evaluation harness — is thin. An evaluation that
runs a COPY of the write path measures the copy; every drift between the two
would show up as a score the real system never earns.

The archive handed to the writer is assembled in two passes, because the two
questions it answers want different lookups:

    before — "what do we already know that is near this?"  → retrieval
    after  — "does anything already claim these keys?"     → exact index seek

The first is fuzzy and keeps the writer from repeating itself. The second is
precise and is what makes a supersede correct. Doing only the first would miss
a collision the shortlist happened not to surface; doing only the second would
leave the writer blind to everything it did not already name.

Unlike the prototype there is no appendTurn here: DARE's chat pipeline already
persisted both messages before this job ever runs, which is exactly the
"transcript must not depend on an extraction working" property, provided by
construction.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from django.db import connection, transaction
from django.db.models import F

from memory.constants import (
    WRITER_RETRIEVE_FLOOR,
    WRITER_RETRIEVE_SHORTLIST_LIMIT,
    WRITER_RETRIEVE_TOP_K,
    MemoryKind,
    MemoryState,
)
from memory.domain.apply import apply_decisions
from memory.domain.types import ApplyInput, ApplyResult, LedgerDraft, MemoryRow
from memory.models import MemoryLedgerEntry, MemoryRecord
from memory.services.embeddings import embed_texts
from memory.services.retrieval import retrieve
from memory.services.store import (
    active_keys,
    find_by_ids,
    find_by_keys,
    parse_iso_date,
    read_user_doc,
    write_user_doc,
)
from memory.services.writer import propose_decisions

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    entries: List[LedgerDraft]
    created: List[MemoryRow]
    retired: int
    reinforced: int
    user_doc_changed: bool
    decisions: int = 0
    model: str = ""
    skipped: Optional[str] = None
    trace: List[str] = field(default_factory=list)


def ingest_turn(
    user,
    conversation,
    user_message,
    ai_message,
    model: Optional[str] = None,
) -> IngestReport:
    """Run the writer over one completed turn and persist what survives the gate.

    Synchronous — this belongs on the single-worker ``memory`` queue, where
    turn N committing before turn N+1 starts is what makes collisions correct.
    """
    user_text = (user_message.message or "").strip()
    assistant_text = (ai_message.message or "").strip()
    if not user_text:
        return _skip("empty user message")

    now = datetime.now(timezone.utc).isoformat()

    # Pass one: what is relevant to this turn. A wider net than the read path,
    # because writing wrongly is more expensive than reading wrongly.
    user_doc = read_user_doc(user)
    recall = retrieve(
        user,
        user_text,
        top_k=WRITER_RETRIEVE_TOP_K,
        floor=WRITER_RETRIEVE_FLOOR,
        shortlist_limit=WRITER_RETRIEVE_SHORTLIST_LIMIT,
        now=now,
    )
    archive: List[MemoryRow] = [item.record for item in recall.chosen]

    # Whether the person asked for something to be kept is the writer's call
    # now, not a phrase list's. A regex knew "remember" and "don't forget" and
    # missed "keep this in mind" — and the thing it gates, a line in the file
    # read on every future turn, is exactly the judgement worth spending a
    # good model on.
    proposal = propose_decisions(
        user_doc=user_doc,
        archive=archive,
        user_message=user_text,
        assistant_message=assistant_text,
        now=now,
        model=model,
        keys_in_use=active_keys(user),
    )
    decisions = proposal.decisions
    explicit = proposal.explicit
    if not decisions:
        return _skip("writer proposed nothing")

    # Pass two: now that the writer has named its keys, look them up exactly.
    # This is the seek the qualified keys were designed for.
    for row in find_by_keys(user, [d.key for d in decisions if d.key]) + find_by_ids(
        user, [d.supersedes_id for d in decisions if d.supersedes_id]
    ):
        if not any(existing.id == row.id for existing in archive):
            archive.append(row)

    apply_input = ApplyInput(
        user_doc=user_doc,
        archive=archive,
        user_message=user_text,
        explicit=explicit,
        now=now,
        new_id=lambda: str(uuid.uuid4()),
        source_conversation_id=conversation.id,
        source_message_id=user_message.id,
    )
    result = apply_decisions(apply_input, decisions)

    # Embed at write time, never at read time. This is the expensive path
    # already — it runs after the reply was delivered — so the cost lands
    # where nobody is waiting, and retrieval never embeds a stored fact.
    # A rule is embedded by the situations it fires in, a fact by what it
    # says. Measured: the terse form of a code-review rule scored 0.17 against
    # "here's my function, take a look" and lost to an unrelated SQL rule;
    # described properly it scores 0.35 and wins.
    vectors = embed_texts([_embedding_text(row) for row in result.created])

    _persist(user, conversation, user_message, archive, result, vectors)

    return IngestReport(
        entries=result.entries,
        created=result.created,
        retired=sum(
            1
            for row in result.archive
            if row.state == MemoryState.SUPERSEDED
            and any(b.id == row.id and b.state == MemoryState.ACTIVE for b in archive)
        ),
        reinforced=len(result.reinforced_ids),
        user_doc_changed=result.user_doc_changed,
        decisions=len(decisions),
        trace=recall.trace,
    )


def _embedding_text(row: MemoryRow) -> str:
    if row.kind == MemoryKind.PROCEDURE and row.applies_when:
        return f"{row.applies_when} {row.text}"
    return f"{row.key} {row.text}"


def _skip(reason: str) -> IngestReport:
    return IngestReport(
        entries=[],
        created=[],
        retired=0,
        reinforced=0,
        user_doc_changed=False,
        skipped=reason,
    )


def _persist(
    user, conversation, user_message, archive_before, result: ApplyResult, vectors
) -> None:
    """Everything or nothing: one transaction, so a ledger row's existence
    proves the whole turn landed — which is what the job's idempotency check
    reads."""
    store_vectors = connection.vendor == "postgresql"

    with transaction.atomic():
        # New rows first: retired rows' superseded_by points at them.
        for row, vector in zip(result.created, vectors):
            MemoryRecord.objects.create(
                id=uuid.UUID(row.id),
                user=user,
                kind=row.kind,
                key=row.key,
                text=row.text,
                state=row.state,
                sensitivity=row.sensitivity,
                source_conversation=conversation,
                source_message=user_message,
                occurred_at=(
                    parse_iso_date(row.occurred_at) if row.occurred_at else None
                ),
                valid_from=parse_iso_date(row.valid_from) if row.valid_from else None,
                valid_until=(
                    parse_iso_date(row.valid_until) if row.valid_until else None
                ),
                replaces_id=uuid.UUID(row.replaces) if row.replaces else None,
                importance=row.importance,
                confidence=row.confidence,
                provenance=row.provenance,
                reinforced=row.reinforced,
                pinned_to=row.pinned_to,
                applies_when=row.applies_when,
                embedding=vector if store_vectors else None,
            )

        # Only rows whose state actually changed, not the whole archive.
        for row in result.archive:
            if row.state != MemoryState.SUPERSEDED:
                continue
            was_active = any(
                before.id == row.id and before.state == MemoryState.ACTIVE
                for before in archive_before
            )
            if not was_active:
                continue
            MemoryRecord.objects.filter(pk=row.id, user=user).update(
                state=MemoryState.SUPERSEDED,
                superseded_by_id=(
                    uuid.UUID(row.superseded_by) if row.superseded_by else None
                ),
                valid_until=(
                    parse_iso_date(row.valid_until) if row.valid_until else None
                ),
            )

        # Nothing was written for these, and that is the point: the person
        # restated something the store already held. The count is what
        # consolidation promotes on.
        for record_id in result.reinforced_ids:
            MemoryRecord.objects.filter(pk=record_id, user=user).update(
                reinforced=F("reinforced") + 1
            )

        if result.user_doc_changed:
            write_user_doc(user, result.user_doc)

        MemoryLedgerEntry.objects.bulk_create(
            MemoryLedgerEntry(
                id=uuid.UUID(entry.id),
                user=user,
                action=entry.action,
                proposed_action=entry.proposed_action,
                reason=entry.reason,
                note=entry.note,
                applied=entry.applied,
                record_id=uuid.UUID(entry.record_id) if entry.record_id else None,
                detail=entry.detail,
                source_text=entry.source_text,
                source_message=user_message,
                proposal=entry.proposal,
            )
            for entry in result.entries
        )
