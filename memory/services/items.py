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
import re
from typing import Any, Dict, List, Optional, Tuple

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


def profile_items(
    document: Optional[UserMemoryDocument],
    pinned: Optional[List[MemoryRecord]] = None,
) -> List[Dict[str, Any]]:
    """The profile as items: pinned facts first, then hand-authored lines.

    A pinned fact keeps its real id, so editing or forgetting a profile line
    goes through the same record path as any other memory — which is what
    makes a profile line correctable at all. Authored lines have no row behind
    them and keep the synthetic content-hash id.
    """
    items: List[Dict[str, Any]] = []

    for record in pinned or []:
        items.append(record_item(record))

    if document is None or not document.content.strip():
        return items

    created = _iso(document.created_at)
    updated = _iso(document.updated_at)
    pinned_text = {(record.text or "").strip().lower() for record in pinned or []}
    for key, lines in parse_user_doc(document.content).items():
        for line in lines:
            # The pinned row already represents this sentence, and it is the
            # copy that can be superseded.
            if line.strip().rstrip(".").lower() in {
                text.rstrip(".") for text in pinned_text
            }:
                continue
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


def behavior_content(key: str, text: str) -> str:
    """A rule with its trigger restored, for display.

    The archive stores a rule's trigger in its key and the rule alone in its
    text — "Use type hints", not "When writing Python, use type hints" — so
    two rules under one trigger can coexist. But a rule shown without its
    trigger reads as a global instruction, and the Memory page's card looks
    for the trigger inside the content to highlight it. So the trigger is
    composed back in here, in exactly the shape the prompt uses.
    """
    trigger = trigger_of(key)
    if not trigger or trigger == key or text.lower().startswith("when "):
        return text
    return f"When {trigger}: {text}"


def behavior_tag(key: str) -> str:
    """The trigger as a hyphenated tag, matching how fact keys read."""
    if not key.startswith("when:"):
        return key
    return key[5:].split(":")[0]


def parse_behavior_content(content: str) -> Tuple[Optional[str], str]:
    """The inverse of :func:`behavior_content` — split an edited rule back into
    its trigger and the rule itself.

    A person editing a card sees "When writing python: Use type hints" and may
    change either half. The trigger belongs in the key and the rule in the
    text, so the two are separated again on the way in rather than storing the
    whole sentence and letting the key drift from what it says.
    """
    match = re.match(r"^\s*when\s+(.+?)\s*[,:]\s*(.+)$", content, re.IGNORECASE)
    if not match:
        return None, content.strip()
    return match.group(1).strip(), match.group(2).strip()


def record_item(record: MemoryRecord, score: Optional[float] = None) -> Dict[str, Any]:
    """One archive row as a compat item.

    A pinned row reads as a profile line wherever it appears — the list, a
    search hit, the response to an edit. Reporting it as knowledge in one
    place and profile in another would move the card between layers the
    moment someone touched it.
    """
    if record.pinned_to:
        memory_type = PROFILE
        categories = [record.pinned_to] + [
            part for part in record.key.split(":") if part
        ]
    elif record.kind == "procedure":
        memory_type = BEHAVIOR
        categories = [behavior_tag(record.key)]
    else:
        memory_type = KNOWLEDGE
        categories = [part for part in record.key.split(":") if part] or [record.kind]
        if record.state == MemoryState.HELD:
            # Visible in the UI, never retrieved — the tag is how the page can
            # say so without a dedicated field in the round-1 contract.
            categories.append("held")

    if record.state == MemoryState.SUPERSEDED:
        categories.append("no-longer-current")

    item: Dict[str, Any] = {
        "id": str(record.id),
        "memory_type": memory_type,
        "content": (
            behavior_content(record.key, record.text)
            if memory_type == BEHAVIOR
            else record.text
        ),
        "categories": categories,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "state": record.state,
        # When it stopped being true, which is a different date from either
        # timestamp above: those say when we found out.
        "valid_until": record.valid_until.isoformat() if record.valid_until else None,
        # What took its place. A retired fact with no visible successor reads
        # as data loss rather than as a correction.
        "replaced_by": (record.superseded_by.text if record.superseded_by_id else None),
    }
    if score is not None:
        item["score"] = round(min(1.0, max(0.0, score)), 4)
    return item


def row_item(row, score: Optional[float] = None) -> Dict[str, Any]:
    """A retrieval-layer MemoryRow as a compat item (dates already ISO)."""
    if row.kind == "procedure":
        memory_type = BEHAVIOR
        categories = [behavior_tag(row.key)]
    else:
        memory_type = KNOWLEDGE
        categories = [part for part in row.key.split(":") if part] or [row.kind]
        if row.state == MemoryState.SUPERSEDED:
            categories.append("no-longer-current")

    item: Dict[str, Any] = {
        "id": row.id,
        "memory_type": memory_type,
        "content": (
            behavior_content(row.key, row.text) if memory_type == BEHAVIOR else row.text
        ),
        "categories": categories,
        "created_at": row.created_at or None,
    }
    if score is not None:
        item["score"] = round(min(1.0, max(0.0, score)), 4)
    return item


def pinned_records(user) -> List[MemoryRecord]:
    """Active facts pinned into the profile, in the order they render."""
    return list(
        MemoryRecord.visible(user)
        .filter(state=MemoryState.ACTIVE)
        .exclude(pinned_to="")
        .order_by("-importance", "created_at")
    )


def listed_records(user, include_retired: bool = False) -> List[MemoryRecord]:
    """Archive rows the list shows: active and held, newest first.

    Retired rows are excluded by default — mixed into a flat list they read as
    contradictory duplicates, one saying Pittsburgh and one saying Boston with
    nothing to say which is current. Asked for explicitly they are their own
    view, where being past IS the subject and the supersession is the point.
    """
    states = [MemoryState.ACTIVE, MemoryState.HELD]
    rows = MemoryRecord.visible(user)
    if include_retired:
        states = [MemoryState.SUPERSEDED]
    else:
        # An ACTIVE pinned fact is shown as a profile line, so listing it here
        # too would put one memory on the page twice under two layers. A
        # RETIRED one is not shown anywhere else — the archive is the only
        # place a replaced profile line can still be seen.
        rows = rows.filter(pinned_to="")
    return list(
        rows.filter(state__in=states)
        .select_related("superseded_by")
        .order_by("-created_at")
    )
