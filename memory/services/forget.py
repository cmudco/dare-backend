"""Erase forgotten memories, leaving only a record that they were forgotten.

The chat transcript is not memory and is never touched here: the message a
memory was learned from stays in its conversation.
"""

from typing import Iterable, List, Set, Tuple

from django.db.models import Q
from django.utils import timezone

from conversations.models import Message
from memory.models import (
    MemoryImportedRecord,
    MemoryLedgerEntry,
    MemoryRecord,
    MemoryStoreOrigin,
    MemoryTombstone,
)

FORGOTTEN = "[forgotten]"
FORGET_REASON = "The person asked for this memory to be forgotten."
CLEAR_REASON = "The person cleared their whole memory."


def erase_memory(user, record: MemoryRecord) -> None:
    """Erase one memory and every earlier version it replaced.

    The statement is also copied into the ledger and into the memory chip on
    the chat reply that wrote it, so those copies go too. Call inside a
    transaction.
    """
    doomed = _with_earlier_versions(user, record)
    ids = [row.id for row in doomed]
    texts = [row.text for row in doomed if row.text]

    quoting = Q(record_id__in=ids)
    for text in texts:
        # Retiring, merging and editing each quote the older statement.
        quoting |= Q(note__contains=text)
    entries = MemoryLedgerEntry.objects.filter(quoting, user=user)

    conversation_ids = {
        row.source_conversation_id for row in doomed if row.source_conversation_id
    }
    conversation_ids.update(
        entries.filter(source_message__isnull=False).values_list(
            "source_message__conversation_id", flat=True
        )
    )
    _scrub_chips(conversation_ids, {str(pk) for pk in ids}, texts)

    entries.exclude(record_id__in=ids).update(note=None)
    entries.filter(record_id__in=ids).update(
        reason="", note=None, detail=FORGOTTEN, source_text="", proposal=None
    )
    leave_tombstones(user, doomed, FORGET_REASON)
    MemoryRecord.objects.filter(user=user, pk__in=ids).delete()


def leave_tombstones(user, records: Iterable[MemoryRecord], reason: str) -> None:
    """Record that these memories were forgotten: when, who and why."""
    now = timezone.now()
    MemoryTombstone.objects.bulk_create(
        [
            MemoryTombstone(
                user=user,
                origin=origin,
                identifier=identifier,
                forgotten_at=now,
                reason=reason,
            )
            for origin, identifier in exchange_identities(user, records)
        ],
        ignore_conflicts=True,
    )


def exchange_identities(user, records: Iterable[MemoryRecord]) -> List[Tuple[str, str]]:
    """Each memory's (origin, identifier) as other stores know it.

    An imported memory keeps the identity its origin gave it; one that began
    here is this store's own.
    """
    imported = {
        record_id: (origin, identifier)
        for record_id, origin, identifier in MemoryImportedRecord.objects.filter(
            user=user
        ).values_list("record_id", "origin", "identifier")
    }
    local = MemoryStoreOrigin.current().label
    return [imported.get(record.id, (local, str(record.id))) for record in records]


def _with_earlier_versions(user, record: MemoryRecord) -> List[MemoryRecord]:
    doomed = {record.id: record}
    frontier = [record.id]
    while frontier:
        earlier = MemoryRecord.objects.filter(
            user=user, superseded_by_id__in=frontier
        ).exclude(pk__in=doomed)
        frontier = []
        for row in earlier:
            doomed[row.id] = row
            frontier.append(row.id)
    return list(doomed.values())


def _scrub_chips(conversation_ids: Set, record_ids: Set[str], texts: List[str]) -> None:
    # _base_manager: a hidden or deleted reply still stores its chip.
    replies = Message._base_manager.filter(
        conversation_id__in=conversation_ids, memory_write_data__isnull=False
    )
    for reply in replies:
        chip = reply.memory_write_data
        changed = False
        for change in chip.get("changes") or []:
            note = change.get("note") or ""
            if change.get("recordId") in record_ids:
                change.update(reason="", note=None, detail=FORGOTTEN)
                changed = True
            elif any(text in note for text in texts):
                change["note"] = None
                changed = True
        if changed:
            Message._base_manager.filter(pk=reply.pk).update(memory_write_data=chip)
