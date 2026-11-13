"""Export a person's memory as an Open Memory Protocol archive."""

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from memory.constants import OMP_WITHHELD_SENSITIVITIES, MemoryState, WriterAction
from memory.domain import omp
from memory.domain.guards import inspect_write
from memory.models import (
    MemoryImportedRecord,
    MemoryLedgerEntry,
    MemoryRecord,
    MemoryStoreOrigin,
    MemoryTombstone,
    UserMemoryDocument,
)
from memory.services.store import row_from_record


def export_archive(user, *, include_sensitive: bool = False) -> Dict[str, Any]:
    """Current memories and the authored profile, as one copy archive.

    Retired memories are history and stay out of a snapshot. Tombstones always
    travel, so a receiver can refuse an older copy of a forgotten memory.
    """
    origin = MemoryStoreOrigin.current()
    person = f"user:{_opaque(origin.id, 'user', user.pk)}"

    current = list(
        MemoryRecord.visible(user)
        .exclude(state=MemoryState.SUPERSEDED)
        .select_related("source_conversation")
        .order_by("created_at")
    )
    shareable = [
        record for record in current if include_sensitive or not _withheld(record)
    ]
    clean = [record for record in shareable if not _has_credential(record)]
    exported_ids = {str(record.id) for record in clean}
    provenance = _provenance(user, person)

    memories: List[Dict[str, Any]] = []
    credentials = len(shareable) - len(clean)

    document = UserMemoryDocument.objects.filter(user=user).first()
    if document is not None and document.content.strip():
        if inspect_write(document.content).credential:
            credentials += 1
        else:
            memories.append(
                omp.document_memory(
                    identifier=_opaque(origin.id, "user-doc", user.pk),
                    markdown=document.content,
                    created_at=document.created_at.isoformat(),
                    updated_at=document.updated_at.isoformat(),
                    scope=[person],
                )
            )

    imported = {
        row.record_id: row for row in MemoryImportedRecord.objects.filter(user=user)
    }
    used = set()
    collisions = 0
    for record in clean:
        arrived = imported.get(record.id)
        if arrived is not None:
            if arrived.identifier in used:
                # Two origins issued the same identifier, and an export needs
                # each one to be unique.
                collisions += 1
                continue
            used.add(arrived.identifier)
            memories.append(
                omp.forwarded_memory(
                    arrived.raw,
                    origin=arrived.origin,
                    identifier=arrived.identifier,
                    body=record.text,
                    status=record.state,
                    provenance=[
                        omp.contributor(
                            WriterAction.IMPORT,
                            arrived.created_at.isoformat(),
                            person,
                        ),
                        *provenance[record.id],
                    ],
                )
            )
            continue
        used.add(str(record.id))
        conversation = record.source_conversation
        scope = [person]
        source_ref = None
        if conversation is not None:
            source_ref = f"dare:conversations/{conversation.conversation_id}"
            if record.source_message_id:
                source_ref += f"#message-{record.source_message_id}"
            if conversation.project_id:
                project = _opaque(origin.id, "project", conversation.project_id)
                scope.append(f"project:{project}")
        memories.append(
            omp.record_memory(
                row_from_record(record),
                updated_at=record.updated_at.isoformat(),
                scope=scope,
                source_ref=source_ref,
                provenance=provenance[record.id],
                exported_ids=exported_ids,
            )
        )

    return {
        "manifest": omp.manifest(
            origin=origin.label,
            exported_at=datetime.now(timezone.utc).isoformat(),
            memories=memories,
            withheld=len(current) - len(shareable),
            credentials=credentials,
            collisions=collisions,
        ),
        "memories": memories,
        "tombstones": [
            omp.tombstone(
                origin=tombstone.origin,
                identifier=tombstone.identifier,
                forgotten_at=tombstone.forgotten_at.isoformat(),
                reason=tombstone.reason,
                person=None if tombstone.import_run_id else person,
            )
            for tombstone in MemoryTombstone.objects.filter(user=user).order_by(
                "forgotten_at"
            )
        ],
    }


def _withheld(record: MemoryRecord) -> bool:
    return (
        record.state == MemoryState.HELD
        or record.sensitivity in OMP_WITHHELD_SENSITIVITIES
    )


def _has_credential(record: MemoryRecord) -> bool:
    return inspect_write(
        f"{record.key}\n{record.applies_when}\n{record.text}\n{record.provenance}"
    ).credential


def _provenance(user, person: str) -> Dict[uuid.UUID, List[Dict[str, str]]]:
    entries = (
        MemoryLedgerEntry.objects.filter(
            user=user,
            applied=True,
            record__isnull=False,
            action__in=omp.CONTRIBUTING_ACTIONS,
        )
        .order_by("created_at")
        .values_list("record_id", "action", "created_at")
    )
    contributors: Dict[uuid.UUID, List[Dict[str, str]]] = defaultdict(list)
    for record_id, action, at in entries:
        contributors[record_id].append(omp.contributor(action, at.isoformat(), person))
    return contributors


def _opaque(origin_id: uuid.UUID, label: str, pk: Any) -> str:
    """A stable id for an internal row that reveals nothing about it."""
    return str(uuid.uuid5(origin_id, f"{label}:{pk}"))
