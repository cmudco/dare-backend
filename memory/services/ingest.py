"""Run one completed turn through the memory write pipeline."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from django.db import connection, transaction
from django.db.models import F
from pgvector.django import CosineDistance

from memory.constants import (
    SNAP_SIMILARITY,
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
from memory.services.ledger import model_from_draft
from memory.services.retrieval import retrieve
from memory.services.store import (
    active_keys,
    find_by_ids,
    find_by_keys,
    parse_iso_date,
    read_user_doc,
)
from memory.services.writer import propose_decisions

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    entries: List[LedgerDraft]
    created: List[MemoryRow]
    retired: int
    reinforced: int
    profile_changed: bool
    decisions: int = 0
    skipped: Optional[str] = None


def ingest_turn(
    user,
    conversation,
    user_message,
    ai_message,
    model: Optional[str] = None,
) -> IngestReport:
    """Propose, gate, embed, and persist one completed turn."""
    user_text = (user_message.message or "").strip()
    assistant_text = (ai_message.message or "").strip()
    if not user_text:
        return _skip("empty user message")

    now = datetime.now(timezone.utc).isoformat()

    # Pass one gives the writer relevant context.
    user_doc = read_user_doc(user)
    recall = retrieve(
        user,
        user_text,
        top_k=WRITER_RETRIEVE_TOP_K,
        floor=WRITER_RETRIEVE_FLOOR,
        relevance_floor=WRITER_RETRIEVE_FLOOR,
        shortlist_limit=WRITER_RETRIEVE_SHORTLIST_LIMIT,
        now=now,
    )
    archive: List[MemoryRow] = [item.record for item in recall.chosen]

    keys_in_use = active_keys(user)
    proposal = propose_decisions(
        user=user,
        source_message_id=user_message.id,
        user_doc=user_doc,
        archive=archive,
        user_message=user_text,
        assistant_message=assistant_text,
        now=now,
        model=model,
        keys_in_use=keys_in_use,
    )
    decisions = proposal.decisions
    explicit = proposal.explicit
    snap_to_existing_slots(user, decisions, keys_in_use)
    if not decisions:
        return _skip("writer proposed nothing")

    # Pass two adds exact key and supersede targets.
    seek = {d.key for d in decisions if d.key}
    for row in find_by_keys(user, sorted(seek)) + find_by_ids(
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

    # Rules embed by trigger; facts embed by key and statement.
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
        profile_changed=result.profile_changed,
        decisions=len(decisions),
    )


def _embedding_text(row: MemoryRow) -> str:
    if row.kind == MemoryKind.PROCEDURE and row.applies_when:
        return f"{row.applies_when} {row.text}"
    return f"{row.key} {row.text}"


def snap_to_existing_slots(user, decisions, keys_in_use) -> None:
    """Snap a new key into a semantically identical existing fact slot."""
    if connection.vendor != "postgresql":
        return
    known = set(keys_in_use)

    def slot_of(decision):
        return decision.key if decision.action == "add_fact" else None

    candidates = [
        d for d in decisions if slot_of(d) and slot_of(d) not in known and d.text
    ]
    if not candidates:
        return

    vectors = embed_texts([f"{slot_of(d)} {d.text}" for d in candidates])
    for decision, vector in zip(candidates, vectors):
        if vector is None:
            continue
        neighbor = (
            MemoryRecord.usable(user)
            .filter(kind=MemoryKind.FACT)
            .exclude(embedding__isnull=True)
            .annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance")
            .first()
        )
        if neighbor is None:
            continue
        similarity = 1.0 - float(neighbor.distance)
        if similarity < SNAP_SIMILARITY or neighbor.key == slot_of(decision):
            continue
        logger.info(
            "[memory] snap: %r -> existing slot %r (%.3f)",
            slot_of(decision),
            neighbor.key,
            similarity,
        )
        decision.reason = (
            f"{decision.reason} [slot: filed under existing key "
            f"'{neighbor.key}' — same fact by meaning, {similarity:.2f}]"
        )
        decision.key = neighbor.key


def _skip(reason: str) -> IngestReport:
    return IngestReport(
        entries=[],
        created=[],
        retired=0,
        reinforced=0,
        profile_changed=False,
        skipped=reason,
    )


def _persist(
    user, conversation, user_message, archive_before, result: ApplyResult, vectors
) -> None:
    """Persist records, state changes, and ledger entries atomically."""
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

        # Restatements only update their durability count.
        for record_id in result.reinforced_ids:
            MemoryRecord.objects.filter(pk=record_id, user=user).update(
                reinforced=F("reinforced") + 1
            )

        for record_id, heading in result.profile_updates.items():
            MemoryRecord.objects.filter(pk=record_id, user=user).update(
                pinned_to=heading
            )

        MemoryLedgerEntry.objects.bulk_create(
            model_from_draft(user, entry, user_message) for entry in result.entries
        )
