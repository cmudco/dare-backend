"""The compat mapping: layered store → the Memory page's flat item list.

The round-1 frontend speaks the old MemU contract — a bare array of
``{id, memoryType, content, categories, createdAt, updatedAt, score?}``. The
layers map onto it:

    USER.md bullet      → memoryType "profile",   categories [heading key]
    fact record         → memoryType "knowledge", categories from the key
    procedure record    → memoryType "behavior",  categories [trigger]

USER.md lines have no ids of their own, so a synthetic, content-stable id is
minted: ``doc:{key}:{sha1(line)[:12]}``. It survives re-listing while the line
is unchanged, and a stale one (the line was edited since) simply fails to
match, which the API reports as 404 rather than deleting the wrong line.
"""

import hashlib
from typing import Any, Dict, List, Optional

from memory.constants import MemoryState
from memory.domain.procedural import trigger_of
from memory.domain.user_doc import parse_user_doc
from memory.models import MemoryRecord, UserMemoryDocument

PROFILE = "profile"
KNOWLEDGE = "knowledge"
BEHAVIOR = "behavior"

DOC_ID_PREFIX = "doc:"


def doc_line_id(key: str, line: str) -> str:
    digest = hashlib.sha1(line.encode("utf-8")).hexdigest()[:12]
    return f"{DOC_ID_PREFIX}{key}:{digest}"


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def profile_items(document: Optional[UserMemoryDocument]) -> List[Dict[str, Any]]:
    """USER.md bullets as compat items, in the document's render order."""
    if document is None or not document.content.strip():
        return []

    items: List[Dict[str, Any]] = []
    created = _iso(document.created_at)
    updated = _iso(document.updated_at)
    for key, lines in parse_user_doc(document.content).items():
        for line in lines:
            items.append(
                {
                    "id": doc_line_id(key, line),
                    "memory_type": PROFILE,
                    "content": line,
                    "categories": [key],
                    "created_at": created,
                    "updated_at": updated,
                }
            )
    return items


def record_item(record: MemoryRecord, score: Optional[float] = None) -> Dict[str, Any]:
    """One archive row as a compat item."""
    if record.kind == "procedure":
        memory_type = BEHAVIOR
        categories = [trigger_of(record.key)]
    else:
        memory_type = KNOWLEDGE
        categories = [part for part in record.key.split(":") if part] or [record.kind]
        if record.state == MemoryState.HELD:
            # Visible in the UI, never retrieved — the tag is how the page can
            # say so without a dedicated field in the round-1 contract.
            categories.append("held")

    item: Dict[str, Any] = {
        "id": str(record.id),
        "memory_type": memory_type,
        "content": record.text,
        "categories": categories,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }
    if score is not None:
        item["score"] = round(min(1.0, max(0.0, score)), 4)
    return item


def row_item(row, score: Optional[float] = None) -> Dict[str, Any]:
    """A retrieval-layer MemoryRow as a compat item (dates already ISO)."""
    if row.kind == "procedure":
        memory_type = BEHAVIOR
        categories = [trigger_of(row.key)]
    else:
        memory_type = KNOWLEDGE
        categories = [part for part in row.key.split(":") if part] or [row.kind]
        if row.state == MemoryState.SUPERSEDED:
            categories.append("no-longer-current")

    item: Dict[str, Any] = {
        "id": row.id,
        "memory_type": memory_type,
        "content": row.text,
        "categories": categories,
        "created_at": row.created_at or None,
    }
    if score is not None:
        item["score"] = round(min(1.0, max(0.0, score)), 4)
    return item


def listed_records(user) -> List[MemoryRecord]:
    """Archive rows the compat list shows: active and held, newest first.

    Superseded rows are deliberately absent — in a flat list they read as
    contradictory duplicates. They surface through the v2 ledger instead.
    """
    return list(
        MemoryRecord.visible(user)
        .filter(state__in=[MemoryState.ACTIVE, MemoryState.HELD])
        .order_by("-created_at")
    )
