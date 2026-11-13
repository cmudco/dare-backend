"""Translate archive rows into Open Memory Protocol memory objects.

OMP is a transfer format, so storage and recall stay as they are and every
field mapping lives in this one module. The target is the editors' draft v0.5
minimal profile, which is not an adopted standard.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import AbstractSet, Any, Dict, List, Optional, Tuple, Union

from memory.constants import (
    OMP_IMPORT_BODY_LIMIT,
    OMP_PROFILE,
    OMP_PROTOCOL_VERSION,
    OMP_TEXT_MEDIA_TYPES,
    OMP_WRITER_ID,
    TOPICS,
    MemoryKind,
    OmpOutcome,
    Sensitivity,
    WriterAction,
)
from memory.domain.guards import inspect_write
from memory.domain.types import MemoryRow

_WRITER_ACTIONS = frozenset(
    {WriterAction.ADD_FACT, WriterAction.ADD_PROCEDURE, WriterAction.SUPERSEDE}
)
_PERSON_CONTRIBUTIONS = {
    WriterAction.EDIT: "edited",
    WriterAction.RELEASE: "approved",
    WriterAction.IMPORT: "imported",
}
CONTRIBUTING_ACTIONS = _WRITER_ACTIONS | frozenset(_PERSON_CONTRIBUTIONS)

_FOLDERS = {MemoryKind.FACT: "facts", MemoryKind.PROCEDURE: "rules"}


def contributor(action: str, at: str, person: str) -> Dict[str, str]:
    """The provenance entry for one applied ledger action on a memory."""
    if action in _WRITER_ACTIONS:
        return {
            "id": OMP_WRITER_ID,
            "kind": "agent",
            "contribution": "authored",
            "at": at,
        }
    return {
        "id": person,
        "kind": "human",
        "contribution": _PERSON_CONTRIBUTIONS[action],
        "at": at,
    }


def record_memory(
    row: MemoryRow,
    *,
    updated_at: str,
    scope: List[str],
    source_ref: Optional[str],
    provenance: List[Dict[str, str]],
    exported_ids: AbstractSet[str],
) -> Dict[str, Any]:
    """One fact or rule as an interchange memory.

    A missing ``provenance`` means the author is unknown, so it is left out
    when the ledger has nothing rather than filled with a guess.
    """
    evidence: List[Any] = []
    if row.provenance:
        evidence.append(row.provenance)
    if source_ref:
        # The transcript stays in DARE, so a receiver cannot follow this.
        evidence.append({"ref": source_ref, "relation": "stated", "dangling": True})

    links: List[Dict[str, Any]] = []
    if row.replaces:
        link: Dict[str, Any] = {"target": row.replaces, "rel": "supersedes"}
        if row.replaces not in exported_ids:
            link["dangling"] = True
        links.append(link)

    tags = [row.kind]
    if row.sensitivity != Sensitivity.NONE:
        tags.append(row.sensitivity)

    return _present(
        {
            "identifier": row.id,
            "protocol_version": OMP_PROTOCOL_VERSION,
            "media_type": "text/plain",
            "path": (
                f"{_FOLDERS[row.kind]}/{row.key.replace(':', '/')}" if row.key else None
            ),
            "tags": tags,
            "scope": scope,
            "created_at": row.created_at,
            "updated_at": updated_at,
            "valid_from": _day_start(row.valid_from),
            "valid_until": _day_end(row.valid_until),
            "status": row.state,
            "weight": row.importance,
            "provenance": provenance,
            "evidence": evidence,
            "links": links,
            "attributes": {
                "dare": _present(
                    {
                        "kind": row.kind,
                        "key": row.key,
                        "sensitivity": row.sensitivity,
                        "confidence": row.confidence,
                        "reinforced": row.reinforced,
                        "occurred_at": row.occurred_at,
                        "pinned_to": row.pinned_to,
                        "applies_when": row.applies_when,
                    }
                )
            },
            "body": row.text,
        }
    )


def forwarded_memory(
    raw: Dict[str, Any],
    *,
    origin: str,
    identifier: str,
    body: str,
    status: str,
    provenance: List[Dict[str, str]],
) -> Dict[str, Any]:
    """An imported memory on its way out again, under its original identity.

    Everything that arrived is passed on, including fields DARE does not use.
    Only what changed here is overlaid, and a checksum that no longer matches
    the text is dropped rather than passed on stale.
    """
    memory = {
        **raw,
        "identifier": identifier,
        "x_omp_origin": origin,
        "body": body,
        "status": status,
    }
    if body != raw.get("body"):
        memory.pop("checksum", None)
    memory["provenance"] = [*raw.get("provenance", []), *provenance]
    return memory


def document_memory(
    *,
    identifier: str,
    markdown: str,
    created_at: str,
    updated_at: str,
    scope: List[str],
) -> Dict[str, Any]:
    """The hand-written half of USER.md as one markdown memory.

    Pinned facts travel as their own memories, so they are not repeated here.
    Both the person and, historically, the writer edited this document, so its
    authorship is left unknown.
    """
    return {
        "identifier": identifier,
        "protocol_version": OMP_PROTOCOL_VERSION,
        "media_type": "text/markdown",
        "path": "USER.md",
        "tags": ["profile"],
        "scope": scope,
        "created_at": created_at,
        "updated_at": updated_at,
        "attributes": {"dare": {"kind": "profile"}},
        "body": markdown,
    }


def tombstone(
    *,
    origin: str,
    identifier: str,
    forgotten_at: str,
    reason: str,
    person: Optional[str],
) -> Dict[str, Any]:
    """A forgotten memory as it travels: when, who and why, never the content.

    ``person`` is left out for a forget another store reported, because who
    did it there is not known here.
    """
    marker: Dict[str, Any] = {
        "origin": origin,
        "identifier": identifier,
        "deleted_at": forgotten_at,
        "reason": reason,
    }
    if person:
        marker["provenance"] = [
            {
                "id": person,
                "kind": "human",
                "contribution": "forgot",
                "at": forgotten_at,
            }
        ]
    return marker


def manifest(
    *,
    origin: str,
    exported_at: str,
    memories: List[Dict[str, Any]],
    withheld: int,
    credentials: int,
    collisions: int,
) -> Dict[str, Any]:
    """The once-per-export header.

    An export that left anything out is a ``selection``, so a receiver never
    reads an absence as a deletion.
    """
    omitted = withheld or credentials or collisions
    result: Dict[str, Any] = {
        "protocol_version": OMP_PROTOCOL_VERSION,
        "profiles": [OMP_PROFILE],
        "origin": origin,
        "exported_at": exported_at,
        "selection": "selection" if omitted else "snapshot",
        "intent": "copy",
        "media_types": sorted({memory["media_type"] for memory in memories}),
    }
    if omitted:
        result["x_dare_omitted"] = {
            "held_or_sensitive": withheld,
            "credentials": credentials,
            "identifier_collisions": collisions,
        }
    return result


def _present(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in fields.items() if value not in (None, "", [])}


def _day_start(day: Optional[str]) -> Optional[str]:
    return f"{day}T00:00:00+00:00" if day else None


def _day_end(day: Optional[str]) -> Optional[str]:
    """A stored end day is inclusive, so the claim stops at the next midnight."""
    if not day:
        return None
    return _day_start((date.fromisoformat(day) + timedelta(days=1)).isoformat())


@dataclass(frozen=True)
class Incoming:
    """One memory from a file, validated and ready to store as held."""

    origin: str
    identifier: str
    kind: str
    key: str
    text: str
    sensitivity: str
    importance: float
    confidence: float
    applies_when: str
    source_sentence: str
    created_at: Optional[datetime]
    occurred_at: Optional[date]
    valid_from: Optional[date]
    valid_until: Optional[date]
    notes: Tuple[str, ...]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class Refusal:
    """One memory from a file that will not be stored, and why."""

    origin: str
    identifier: str
    status: str
    reason: str


_KEY = re.compile(r"^[a-z0-9_\-]+(:[a-z0-9_\-]+)*$")
_LIST_FIELDS = ("tags", "scope", "provenance", "evidence", "links")


def read_memory(raw: Any, manifest_origin: str) -> Union[Incoming, Refusal]:
    """Decide whether one memory object from a file can be stored.

    A file is a set of claims by its origin, so nothing in it raises trust:
    a safety label and a profile pin are not carried over, and the caller
    stores whatever is returned as held.
    """
    if not isinstance(raw, dict):
        return Refusal(manifest_origin, "", OmpOutcome.REJECTED, "Not a memory.")
    identifier = raw.get("identifier")
    origin = raw.get("x_omp_origin", manifest_origin)

    def refuse(reason: str, status: str = OmpOutcome.REJECTED) -> Refusal:
        return Refusal(
            origin if _label(origin) else manifest_origin,
            identifier if _label(identifier) else "",
            status,
            reason,
        )

    if not _label(identifier) or not _label(origin):
        return refuse("It has no usable identifier.")
    if any(not isinstance(raw.get(field, []), list) for field in _LIST_FIELDS):
        return refuse("Its fields are malformed.")
    media_type = raw.get("media_type")
    if (
        not isinstance(media_type, str)
        or media_type.split(";")[0].strip().lower() not in OMP_TEXT_MEDIA_TYPES
    ):
        return refuse("Only plain text and markdown memories can be imported.")

    attributes = raw.get("attributes")
    dare = attributes.get("dare") if isinstance(attributes, dict) else None
    dare = dare if isinstance(dare, dict) else {}
    if dare.get("kind") == "profile":
        return refuse(
            "A hand-written profile is not imported. Copy the lines you want.",
            OmpOutcome.OMITTED,
        )

    body = raw.get("body")
    text = body.strip() if isinstance(body, str) else ""
    if not text:
        return refuse("It has no text.")
    if len(text) > OMP_IMPORT_BODY_LIMIT:
        return refuse("It is longer than a memory here can be.")

    offered = dare.get("key")
    key = offered if isinstance(offered, str) and _usable_key(offered) else ""
    is_rule = dare.get("kind") == MemoryKind.PROCEDURE and key.startswith("when:")
    if not is_rule and key.startswith("when:"):
        key = ""
    applies_when = str(dare.get("applies_when") or "")[:300] if is_rule else ""
    source_sentence = next(
        (item[:400] for item in raw.get("evidence", []) if isinstance(item, str)), ""
    )

    policy = inspect_write("\n".join([key, applies_when, text, source_sentence]))
    if policy.credential:
        return refuse("It contains a credential.")
    if policy.override:
        return refuse("It tries to override instructions.")

    notes = []
    sensitivity = dare.get("sensitivity")
    if sensitivity == Sensitivity.SAFETY:
        notes.append("Its safety label was not carried over.")
    if sensitivity not in (Sensitivity.HEALTH, Sensitivity.THIRD_PARTY):
        sensitivity = Sensitivity.NONE
    if dare.get("pinned_to"):
        notes.append("It is no longer pinned to the profile.")

    return Incoming(
        origin=origin,
        identifier=identifier,
        kind=MemoryKind.PROCEDURE if is_rule else MemoryKind.FACT,
        key=key,
        text=text,
        sensitivity=sensitivity,
        # A weight only orders memories from one store, so it is used only
        # when that store shares DARE's scale.
        importance=_unit(raw.get("weight"), 0.5) if dare else 0.5,
        confidence=_unit(dare.get("confidence"), 0.9),
        applies_when=applies_when,
        source_sentence=source_sentence,
        created_at=_moment(raw.get("created_at")),
        occurred_at=_day(dare.get("occurred_at")),
        valid_from=_day(raw.get("valid_from")),
        valid_until=_last_day(raw.get("valid_until")),
        notes=tuple(notes),
        raw=raw,
    )


def read_tombstone(raw: Any) -> Optional[Tuple[str, str, Optional[datetime], str]]:
    """A file's forget marker as (origin, identifier, when, why), if well formed."""
    if not isinstance(raw, dict):
        return None
    origin, identifier = raw.get("origin"), raw.get("identifier")
    if not _label(origin) or not _label(identifier):
        return None
    reason = raw.get("reason")
    return (
        origin,
        identifier,
        _moment(raw.get("deleted_at")),
        reason[:255] if isinstance(reason, str) else "",
    )


def _label(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) and len(value) <= 255


def _usable_key(key: str) -> bool:
    if len(key) > 255 or not _KEY.match(key):
        return False
    topic = key.split(":")[0]
    return topic in TOPICS or (topic == "when" and ":" in key)


def _unit(value: Any, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return min(1.0, max(0.0, float(value)))


def _moment(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day(value: Any) -> Optional[date]:
    moment = _moment(value)
    return moment.astimezone(timezone.utc).date() if moment else None


def _last_day(value: Any) -> Optional[date]:
    """The inverse of :func:`_day_end`: the last day a claim still held."""
    moment = _moment(value)
    if moment is None:
        return None
    return (moment.astimezone(timezone.utc) - timedelta(microseconds=1)).date()


def identity_map(manifest: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """Original identities for memories a sender had to rename.

    When one export carries the same identifier from two origins, the sender
    renames them and lists the originals in ``x_omp_identity_map``. This is a
    convention from the Cloudflare example, not adopted protocol text.
    """
    originals: Dict[str, Tuple[str, str]] = {}
    listed = manifest.get("x_omp_identity_map")
    for item in listed if isinstance(listed, list) else []:
        if (
            isinstance(item, dict)
            and _label(item.get("exported_identifier"))
            and _label(item.get("origin"))
            and _label(item.get("identifier"))
        ):
            originals[item["exported_identifier"]] = (
                item["origin"],
                item["identifier"],
            )
    return originals
