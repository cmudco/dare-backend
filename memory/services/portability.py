"""Export exact memory state and import trusted bundles or foreign text."""

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.constants import (
    TOKEN_BUDGET,
    MemoryKind,
    MemoryState,
    Sensitivity,
    WriterAction,
)
from memory.domain.guards import inspect_write
from memory.domain.user_doc import (
    PROFILE_HEADINGS,
    admitted_pins,
    estimate_tokens,
    normalize_user_doc,
)
from memory.models import MemoryRecord
from memory.services.embeddings import embed_texts
from memory.services.ledger import LedgerEvent, record_event
from memory.services.store import read_authored_doc, read_user_doc, write_user_doc
from memory.tasks import run_memory_writer

logger = logging.getLogger(__name__)

SCHEMA = "dare-memory-v2"

# A person's real store measures in the hundreds; ten times the biggest store
# seen in testing. Above this, refuse rather than time out embedding.
MAX_RECORDS = 3000

_KINDS = {choice for choice, _ in MemoryKind.choices}
_STATES = {choice for choice, _ in MemoryState.choices}
_SENSITIVITIES = {choice for choice, _ in Sensitivity.choices}


def export_bundle(user) -> Dict[str, Any]:
    """Export visible rows, relationships, and authored USER.md."""
    records = (
        MemoryRecord.visible(user)
        .select_related("superseded_by", "replaces")
        .order_by("created_at")
    )
    return {
        "schema": SCHEMA,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "document": read_authored_doc(user),
        "records": [_record_payload(record) for record in records],
    }


def _record_payload(record: MemoryRecord) -> Dict[str, Any]:
    return {
        "id": str(record.id),
        "kind": record.kind,
        "key": record.key,
        "text": record.text,
        "state": record.state,
        "sensitivity": record.sensitivity,
        "pinned_to": record.pinned_to,
        "applies_when": record.applies_when,
        "importance": record.importance,
        "confidence": record.confidence,
        "provenance": record.provenance,
        "reinforced": record.reinforced,
        "occurred_at": _day(record.occurred_at),
        "valid_from": _day(record.valid_from),
        "valid_until": _day(record.valid_until),
        "superseded_by": (
            str(record.superseded_by_id) if record.superseded_by_id else None
        ),
        "replaces": str(record.replaces_id) if record.replaces_id else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _day(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


class ImportError_(Exception):
    """A readable import refusal."""


def import_bundle(user, data: Any) -> Dict[str, Any]:
    """Restore a bundle into an empty store with fresh ids and embeddings."""
    document, rows = _validated(data)

    if MemoryRecord.visible(user).exists() or read_user_doc(user).strip():
        raise ImportError_(
            "Import needs an empty store. Export first if anything here "
            "matters, then Forget everything, then import."
        )

    id_map = {row["id"]: uuid.uuid4() for row in rows}

    texts = [
        (
            f"{row['applies_when']} {row['text']}"
            if row["kind"] == MemoryKind.PROCEDURE and row["applies_when"]
            else f"{row['key']} {row['text']}"
        )
        for row in rows
    ]
    vectors = embed_texts(texts) if rows else []

    with transaction.atomic():
        for row, vector in zip(rows, vectors):
            record = MemoryRecord.objects.create(
                id=id_map[row["id"]],
                user=user,
                kind=row["kind"],
                key=row["key"],
                text=row["text"],
                state=row["state"],
                sensitivity=row["sensitivity"],
                pinned_to=row["pinned_to"],
                applies_when=row["applies_when"],
                importance=row["importance"],
                confidence=row["confidence"],
                provenance=row["provenance"],
                reinforced=row["reinforced"],
                occurred_at=row["occurred_at"],
                valid_from=row["valid_from"],
                valid_until=row["valid_until"],
                embedding=vector,
            )
            if row["created_at"] is not None:
                MemoryRecord.objects.filter(pk=record.pk).update(
                    created_at=row["created_at"]
                )
        # Chains second, once every endpoint exists.
        for row in rows:
            links = {}
            if row["superseded_by"] in id_map:
                links["superseded_by_id"] = id_map[row["superseded_by"]]
            if row["replaces"] in id_map:
                links["replaces_id"] = id_map[row["replaces"]]
            if links:
                MemoryRecord.objects.filter(pk=id_map[row["id"]]).update(**links)

        if document.strip():
            write_user_doc(user, document)

        record_event(
            user,
            LedgerEvent(
                action=WriterAction.IMPORT,
                reason="The person imported a memory bundle.",
                note=(
                    f"Reinstated {len(rows)} memories and the profile document "
                    f"from a {SCHEMA} bundle."
                ),
                applied=True,
                detail=f"{len(rows)} records",
            ),
        )

    embedded = sum(1 for vector in vectors if vector is not None)
    logger.info(
        "[memory] import for user %s: %d records (%d embedded), doc=%s",
        user.id,
        len(rows),
        embedded,
        bool(document.strip()),
    )
    return {
        "records": len(rows),
        "embedded": embedded,
        "document": bool(document.strip()),
    }


def _validated(data: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """Coerce and validate an untrusted bundle."""
    if not isinstance(data, dict):
        raise ImportError_("Not a memory bundle.")
    if data.get("schema") != SCHEMA:
        raise ImportError_(
            f"Unrecognized bundle schema {data.get('schema')!r} — this build "
            f"imports {SCHEMA}."
        )
    raw_rows = data.get("records")
    if not isinstance(raw_rows, list):
        raise ImportError_("The bundle has no record list.")
    if len(raw_rows) > MAX_RECORDS:
        raise ImportError_(f"Too many records ({len(raw_rows)} > {MAX_RECORDS}).")

    document = data.get("document")
    document = normalize_user_doc(document) if isinstance(document, str) else ""
    document_policy = inspect_write(document)
    if document_policy.credential:
        raise ImportError_("The imported USER.md contains a credential.")
    if document_policy.override:
        raise ImportError_("The imported USER.md contains an instruction override.")
    if estimate_tokens(document) > TOKEN_BUDGET:
        raise ImportError_(
            f"The imported USER.md exceeds the {TOKEN_BUDGET}-token ceiling."
        )

    rows: List[Dict[str, Any]] = []
    ids = set()
    active_slots = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise ImportError_(f"Record {index} is not an object.")
        text = str(raw.get("text") or "").strip()
        if not text:
            # A memory with no statement is nothing; skip rather than refuse
            # the whole bundle over one damaged row.
            continue
        kind = raw.get("kind")
        if kind not in _KINDS:
            kind = MemoryKind.FACT
        state = raw.get("state")
        if state not in _STATES:
            state = MemoryState.ACTIVE
        sensitivity = raw.get("sensitivity")
        if sensitivity not in _SENSITIVITIES:
            sensitivity = Sensitivity.NONE
        row_id = str(raw.get("id") or f"row-{index}")
        if row_id in ids:
            raise ImportError_(f"Duplicate record id: {row_id}.")
        ids.add(row_id)
        key = str(raw.get("key") or "")[:255]
        applies_when = str(raw.get("applies_when") or "")[:300]
        pinned_to = str(raw.get("pinned_to") or "")[:40]
        if pinned_to not in PROFILE_HEADINGS:
            pinned_to = ""
        provenance = str(raw.get("provenance") or "")[:400]
        policy = inspect_write(f"{key}\n{applies_when}\n{text}\n{provenance}")
        if policy.credential:
            raise ImportError_(f"Record {index} contains a credential.")
        if policy.override:
            raise ImportError_(f"Record {index} contains an instruction override.")
        slot = (kind, key)
        if state == MemoryState.ACTIVE and slot in active_slots:
            raise ImportError_(f"More than one active record uses {kind}:{key}.")
        if state == MemoryState.ACTIVE:
            active_slots.add(slot)
        rows.append(
            {
                "id": row_id,
                "kind": kind,
                "key": key,
                "text": text[:4000],
                "state": state,
                "sensitivity": sensitivity,
                "pinned_to": pinned_to,
                "applies_when": applies_when,
                "importance": _unit(raw.get("importance"), 0.5),
                "confidence": _unit(raw.get("confidence"), 0.9),
                "provenance": provenance,
                "reinforced": max(0, _int(raw.get("reinforced"), 0)),
                "occurred_at": _parse_day(raw.get("occurred_at")),
                "valid_from": _parse_day(raw.get("valid_from")),
                "valid_until": _parse_day(raw.get("valid_until")),
                "superseded_by": raw.get("superseded_by"),
                "replaces": raw.get("replaces"),
                "created_at": _parse_datetime(raw.get("created_at")),
            }
        )
    pinned = sorted(
        (
            row
            for row in rows
            if row["state"] == MemoryState.ACTIVE and row["pinned_to"]
        ),
        key=lambda row: (
            -row["importance"],
            row["created_at"] or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )
    requested = [
        (
            row["pinned_to"],
            row["text"],
            row["sensitivity"] == Sensitivity.SAFETY,
        )
        for row in pinned
    ]
    if len(admitted_pins(document, requested, TOKEN_BUDGET)) != len(requested):
        raise ImportError_(
            f"The imported profile pins exceed the {TOKEN_BUDGET}-token ceiling."
        )

    return document, rows


# Foreign text goes through the ordinary writer and gate.
FOREIGN_CHUNK_CHARS = 1200
FOREIGN_MAX_CHARS = 20000

FOREIGN_FRAME = (
    "I'm importing my memories from another AI assistant. Everything below "
    "is what it knew about me — treat each line as something I am telling "
    "you about myself, in my own words:\n\n"
)


def import_foreign(user, text: Any) -> Dict[str, Any]:
    """Queue a free-form paste through the ordinary writer and gate."""
    body = str(text or "").strip()
    if not body:
        raise ImportError_("Nothing to import — the paste is empty.")
    if len(body) > FOREIGN_MAX_CHARS:
        raise ImportError_(
            f"That paste is too large ({len(body)} characters; the limit is "
            f"{FOREIGN_MAX_CHARS}). Split it and import in parts."
        )

    chunks = _chunked(body)

    with transaction.atomic():
        conversation = Conversation.active_objects.create(
            user=user,
            conversation_id=f"import-{uuid.uuid4().hex[:10]}",
            title="Imported memories",
            memory_enabled=True,
        )
        ai_message_ids = []
        for chunk in chunks:
            Message.active_objects.create(
                conversation=conversation,
                sender_type=SenderType.PLAYER,
                message=FOREIGN_FRAME + chunk,
            )
            reply = Message.active_objects.create(
                conversation=conversation,
                sender_type=SenderType.AI_ASSISTANT,
                message="This import chunk was queued for memory review.",
            )
            ai_message_ids.append(reply.id)

    for message_id in ai_message_ids:
        run_memory_writer.delay(message_id)

    logger.info(
        "[memory] foreign import for user %s: %d chars in %d chunks -> %s",
        user.id,
        len(body),
        len(chunks),
        conversation.conversation_id,
    )
    return {
        "queued_chunks": len(chunks),
        "conversation_id": conversation.conversation_id,
    }


def _chunked(body: str) -> List[str]:
    """Split on line boundaries without cutting a fact in half."""
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for line in body.splitlines():
        line_size = len(line) + 1
        if current and size + line_size > FOREIGN_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks


def _unit(value: Any, fallback: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_day(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
