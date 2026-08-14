"""Export and import of one person's memory store — the whole layered
contract, in a shape that survives the trip.

The predecessor (the MemU-era export PRs) serialized a flat item list, which
could show a store but never reinstate one: supersession chains, held rows,
pinned facts and the hand-authored document had nowhere to live in it. This
bundle carries the actual contract, so export → forget everything → import
puts the store back the way it was — retired history, profile and all.

What is deliberately NOT in the bundle:

- Embeddings. Half a megabyte of floats that another instance can recompute
  for cents, and that would pin the bundle to one embedding model forever.
  Import re-embeds.
- The ledger. It is the audit trail of what THIS account's writer and gate
  did; importing it elsewhere would fabricate a history nobody there enacted.
  The import writes its own single ledger row saying what arrived.
- Conversations. The transcript layer belongs to the conversations feature;
  a memory bundle that quietly carried every word ever said would be a
  surprise with legal weight. (The old export offered it as an explicit
  separate scope, and a successor can again.)
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

from memory.constants import MemoryKind, MemoryState, Sensitivity, WriterAction
from memory.domain.user_doc import normalize_user_doc
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.embeddings import embed_texts
from memory.services.store import read_user_doc, write_user_doc

logger = logging.getLogger(__name__)

SCHEMA = "dare-memory-v2"

# A person's real store measures in the hundreds; ten times the biggest store
# seen in testing. Above this, refuse rather than time out embedding.
MAX_RECORDS = 3000

_KINDS = {choice for choice, _ in MemoryKind.choices}
_STATES = {choice for choice, _ in MemoryState.choices}
_SENSITIVITIES = {choice for choice, _ in Sensitivity.choices}


def export_bundle(user) -> Dict[str, Any]:
    """The store as a self-contained document: every visible row, every
    relationship, and the profile markdown."""
    records = (
        MemoryRecord.visible(user)
        .select_related("superseded_by", "replaces")
        .order_by("created_at")
    )
    return {
        "schema": SCHEMA,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "document": read_user_doc(user),
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
    """A bundle that cannot be imported, with a reason a person can read."""


def import_bundle(user, data: Any) -> Dict[str, Any]:
    """Reinstate an exported store, whole.

    Requires an EMPTY store. Merging an import into live memories would need
    answers to questions this feature should not invent (which location wins?
    do chains interleave?), and the flow it exists for — new account, or
    forget-everything then restore — starts empty anyway. Refusing loudly
    beats merging wrongly.

    Ids are minted fresh (a bundle is data, not authority over primary keys)
    with the supersession chains remapped onto the new ids. Rows are
    re-embedded here, which is what frees the bundle from ever naming an
    embedding model.
    """
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
            MemoryRecord.objects.create(
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

        MemoryLedgerEntry.objects.create(
            user=user,
            action=WriterAction.IMPORT,
            proposed_action=WriterAction.IMPORT,
            reason="The person imported a memory bundle.",
            note=(
                f"Reinstated {len(rows)} memories and the profile document "
                f"from a {SCHEMA} bundle."
            ),
            applied=True,
            detail=f"{len(rows)} records",
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
    """The bundle, coerced and checked — or a readable refusal.

    Hardened the way the old import PR learned to be: every field coerced to
    its type, enums checked against the real vocabularies, links to unknown
    ids dropped rather than trusted. A bundle is user input.
    """
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

    rows: List[Dict[str, Any]] = []
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
        rows.append(
            {
                "id": str(raw.get("id") or f"row-{index}"),
                "kind": kind,
                "key": str(raw.get("key") or "")[:255],
                "text": text[:4000],
                "state": state,
                "sensitivity": sensitivity,
                "pinned_to": str(raw.get("pinned_to") or "")[:40],
                "applies_when": str(raw.get("applies_when") or "")[:300],
                "importance": _unit(raw.get("importance"), 0.5),
                "confidence": _unit(raw.get("confidence"), 0.9),
                "provenance": str(raw.get("provenance") or "")[:400],
                "reinforced": max(0, _int(raw.get("reinforced"), 0)),
                "occurred_at": _parse_day(raw.get("occurred_at")),
                "valid_from": _parse_day(raw.get("valid_from")),
                "valid_until": _parse_day(raw.get("valid_until")),
                "superseded_by": raw.get("superseded_by"),
                "replaces": raw.get("replaces"),
            }
        )
    return document, rows


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
