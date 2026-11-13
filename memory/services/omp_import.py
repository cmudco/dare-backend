"""Import an Open Memory Protocol archive into a person's memory.

A file is a set of claims by the store that wrote it. Each memory is decided
on its own, nothing here replaces or deletes a memory the person already has,
and whatever is accepted waits as held until the person releases it.
"""

import hashlib
import logging
from dataclasses import replace
from typing import Any, Dict, List, Set, Tuple

from django.db import transaction
from django.utils import timezone

from memory.constants import (
    OMP_IMPORT_MAX_RECORDS,
    OMP_IMPORT_VERSIONS,
    OMP_WITHHELD_SENSITIVITIES,
    MemoryKind,
    MemoryState,
    OmpOutcome,
    WriterAction,
)
from memory.domain import omp
from memory.models import (
    MemoryImportedRecord,
    MemoryImportRun,
    MemoryRecord,
    MemoryStoreOrigin,
    MemoryTombstone,
)
from memory.services.embeddings import embed_texts
from memory.services.ledger import LedgerEvent, record_event

logger = logging.getLogger(__name__)

Identity = Tuple[str, str]


class OmpImportError(Exception):
    """A readable reason the whole file was refused."""


def import_archive(user, data: Any) -> MemoryImportRun:
    """Store what the file offers and return the run with its receipt."""
    origin, raw_memories, raw_tombstones = _envelope(data)
    originals = omp.identity_map(data["manifest"])
    local = MemoryStoreOrigin.current().label

    forgotten: Set[Identity] = set(
        MemoryTombstone.objects.filter(user=user).values_list("origin", "identifier")
    )
    # What is already here, keyed the way a file names it, with its text.
    here: Dict[Identity, str] = {
        (local, str(pk)): text
        for pk, text in MemoryRecord.objects.filter(user=user).values_list("pk", "text")
    }
    here.update(
        {
            (row_origin, identifier): text
            for row_origin, identifier, text in MemoryImportedRecord.objects.filter(
                user=user
            ).values_list("origin", "identifier", "record__text")
        }
    )

    entries: List[Dict[str, Any]] = []
    markers = []
    for raw in raw_tombstones:
        marker = omp.read_tombstone(raw)
        if marker is None:
            continue
        identity = marker[:2]
        if identity in here:
            entries.append(
                _entry(
                    "tombstone",
                    identity,
                    OmpOutcome.OMITTED,
                    "The file says this was forgotten elsewhere. It was kept "
                    "here; forget it yourself if you want it gone.",
                )
            )
        elif identity in forgotten:
            entries.append(_entry("tombstone", identity, OmpOutcome.UNCHANGED))
        else:
            forgotten.add(identity)
            markers.append(marker)
            entries.append(_entry("tombstone", identity, OmpOutcome.ACCEPTED))

    accepted: List[omp.Incoming] = []
    seen: Set[Identity] = set()
    for raw in raw_memories:
        parsed = omp.read_memory(raw, origin)
        if parsed.identifier in originals:
            parsed = replace(
                parsed,
                origin=originals[parsed.identifier][0],
                identifier=originals[parsed.identifier][1],
            )
        identity = (parsed.origin, parsed.identifier)
        if isinstance(parsed, omp.Refusal):
            entries.append(_entry("memory", identity, parsed.status, parsed.reason))
        elif identity in seen:
            entries.append(
                _entry(
                    "memory",
                    identity,
                    OmpOutcome.REJECTED,
                    "It appears more than once in this file.",
                )
            )
        elif identity in forgotten:
            entries.append(
                _entry(
                    "memory",
                    identity,
                    OmpOutcome.REJECTED,
                    "It was forgotten, so it was not brought back.",
                )
            )
        elif identity in here:
            same = here[identity] == parsed.text
            entries.append(
                _entry(
                    "memory",
                    identity,
                    OmpOutcome.UNCHANGED if same else OmpOutcome.REJECTED,
                    "" if same else "A different version is already here.",
                )
            )
        else:
            accepted.append(parsed)
        seen.add(identity)

    # Embedding is a network call, so it happens before the transaction opens.
    vectors = (
        embed_texts([_embedding_text(item) for item in accepted]) if accepted else []
    )

    with transaction.atomic():
        run = MemoryImportRun.objects.create(user=user, origin=origin)
        MemoryTombstone.objects.bulk_create(
            [
                MemoryTombstone(
                    user=user,
                    origin=marker_origin,
                    identifier=identifier,
                    forgotten_at=forgotten_at or timezone.now(),
                    reason=reason,
                    import_run=run,
                )
                for marker_origin, identifier, forgotten_at, reason in markers
            ],
            ignore_conflicts=True,
        )
        taken = set(MemoryRecord.objects.filter(user=user).values_list("kind", "key"))
        current = set(
            MemoryRecord.usable(user).values_list("kind", "key"),
        )
        for item, vector in zip(accepted, vectors):
            key = _free_key(item, taken)
            taken.add((item.kind, key))
            record = MemoryRecord.objects.create(
                user=user,
                kind=item.kind,
                key=key,
                text=item.text,
                state=MemoryState.HELD,
                sensitivity=item.sensitivity,
                importance=item.importance,
                confidence=item.confidence,
                provenance=item.source_sentence,
                applies_when=item.applies_when,
                occurred_at=item.occurred_at,
                valid_from=item.valid_from,
                valid_until=item.valid_until,
                embedding=vector,
            )
            if item.created_at is not None:
                MemoryRecord.objects.filter(pk=record.pk).update(
                    created_at=item.created_at
                )
            MemoryImportedRecord.objects.create(
                user=user,
                record=record,
                import_run=run,
                origin=item.origin,
                identifier=item.identifier,
                raw=item.raw,
            )
            notes = list(item.notes)
            if item.key and (item.kind, item.key) in current:
                notes.append(
                    "You already have a memory on this topic. Check that the "
                    "two agree before releasing it."
                )
            entries.append(
                _entry(
                    "memory",
                    (item.origin, item.identifier),
                    OmpOutcome.TRANSFORMED if item.notes else OmpOutcome.ACCEPTED,
                    " ".join(notes),
                    record_id=str(record.id),
                )
            )

        run.entries = entries
        run.save(update_fields=["entries", "updated_at"])
        record_event(
            user,
            LedgerEvent(
                action=WriterAction.IMPORT,
                reason="The person imported an Open Memory Protocol file.",
                note=(
                    f"Stored {len(accepted)} memories as held and left out "
                    f"{len(raw_memories) - len(accepted)}. The receipt lists each one."
                ),
                applied=True,
                detail=f"Import {run.id} from {origin}",
            ),
        )

    logger.info(
        "[memory] omp import for user %s from %s: %d stored, %d left out, "
        "%d tombstones",
        user.id,
        origin,
        len(accepted),
        len(raw_memories) - len(accepted),
        len(markers),
    )
    return run


def release_imports(user) -> Dict[str, int]:
    """Release every imported memory still held, except what stays gated.

    Memories labelled health or third-party are released one at a time, by
    hand, the same as ones learned in chat.
    """
    held = _held_imports(user)
    released = 0
    with transaction.atomic():
        for record in held.exclude(sensitivity__in=OMP_WITHHELD_SENSITIVITIES):
            record.state = MemoryState.ACTIVE
            record.save(update_fields=["state", "updated_at"])
            record_event(
                user,
                LedgerEvent(
                    action=WriterAction.RELEASE,
                    reason="The user released this imported memory.",
                    applied=True,
                    record=record,
                    detail=record.text,
                ),
            )
            released += 1
    return {"released": released, "still_held": held.count()}


def held_imports_count(user) -> int:
    return _held_imports(user).count()


def _held_imports(user):
    return MemoryRecord.visible(user).filter(
        imported__isnull=False, state=MemoryState.HELD
    )


def _envelope(data: Any) -> Tuple[str, List[Any], List[Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("manifest"), dict):
        raise OmpImportError("This is not an Open Memory Protocol file.")
    manifest = data["manifest"]
    version = manifest.get("protocol_version")
    if version not in OMP_IMPORT_VERSIONS:
        raise OmpImportError(
            f"This file uses protocol version {version!r}. Versions "
            f"{', '.join(sorted(OMP_IMPORT_VERSIONS))} can be imported."
        )
    origin = manifest.get("origin")
    if not isinstance(origin, str) or not origin.strip() or len(origin) > 255:
        raise OmpImportError("The file does not say which store it came from.")
    if manifest.get("intent", "copy") != "copy":
        raise OmpImportError("Only a copy can be imported, not a move.")
    memories = data.get("memories")
    tombstones = data.get("tombstones", [])
    if not isinstance(memories, list) or not isinstance(tombstones, list):
        raise OmpImportError("The file has no list of memories.")
    if len(memories) > OMP_IMPORT_MAX_RECORDS:
        raise OmpImportError(
            f"The file has {len(memories)} memories; the limit is "
            f"{OMP_IMPORT_MAX_RECORDS}."
        )
    return origin, memories, tombstones


def _entry(
    kind: str,
    identity: Identity,
    status: str,
    reason: str = "",
    record_id: str = "",
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "origin": identity[0],
        "identifier": identity[1],
        "status": status,
        "reason": reason,
        "record_id": record_id,
    }


def _free_key(item: omp.Incoming, taken: Set[Tuple[str, str]]) -> str:
    """The file's key when nothing here uses it, else one that cannot collide.

    Two memories of one kind under one key cannot both be true, and an import
    never retires a memory, so a taken key is replaced instead.
    """
    if item.key and (item.kind, item.key) not in taken:
        return item.key
    digest = hashlib.sha1(f"{item.origin}\n{item.identifier}".encode()).hexdigest()
    if item.kind == MemoryKind.PROCEDURE:
        return f"{item.key}-{digest[:8]}"
    return f"note:imported-{digest[:8]}"


def _embedding_text(item: omp.Incoming) -> str:
    if item.kind == MemoryKind.PROCEDURE and item.applies_when:
        return f"{item.applies_when} {item.text}"
    return f"{item.key} {item.text}"
