"""Finish earlier forgets.

Forgetting used to hide a memory and keep its text, and copied that text into
the ledger and the chat reply's memory chip. This erases those copies and
leaves a tombstone, which is what forgetting does from now on. It cannot be
reversed.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

FORGOTTEN = "[forgotten]"
REASON = "The person asked for this memory to be forgotten."


def erase_previously_forgotten(apps, schema_editor):
    MemoryRecord = apps.get_model("memory", "MemoryRecord")
    MemoryLedgerEntry = apps.get_model("memory", "MemoryLedgerEntry")
    MemoryStoreOrigin = apps.get_model("memory", "MemoryStoreOrigin")
    MemoryTombstone = apps.get_model("memory", "MemoryTombstone")
    Message = apps.get_model("conversations", "Message")

    hidden = MemoryRecord.objects.filter(is_deleted=True)
    if not hidden.exists():
        return
    origin, _ = MemoryStoreOrigin.objects.get_or_create(singleton=True)

    for record in hidden.iterator():
        record_id = str(record.id)
        quoting = Q(record_id=record.id)
        if record.text:
            quoting |= Q(note__contains=record.text)
        entries = MemoryLedgerEntry.objects.filter(quoting, user_id=record.user_id)

        conversation_ids = set(
            entries.filter(source_message__isnull=False).values_list(
                "source_message__conversation_id", flat=True
            )
        )
        if record.source_conversation_id:
            conversation_ids.add(record.source_conversation_id)
        # Message has no plain manager; a hidden reply still stores its chip.
        replies = Message._base_manager.filter(
            conversation_id__in=conversation_ids, memory_write_data__isnull=False
        )
        for reply in replies:
            chip = reply.memory_write_data
            changed = False
            for change in chip.get("changes") or []:
                if change.get("recordId") == record_id:
                    change.update(reason="", note=None, detail=FORGOTTEN)
                    changed = True
                elif record.text and record.text in (change.get("note") or ""):
                    change["note"] = None
                    changed = True
            if changed:
                Message._base_manager.filter(pk=reply.pk).update(memory_write_data=chip)

        entries.exclude(record_id=record.id).update(note=None)
        entries.filter(record_id=record.id).update(
            reason="", note=None, detail=FORGOTTEN, source_text="", proposal=None
        )
        MemoryTombstone.objects.get_or_create(
            user_id=record.user_id,
            origin=f"dare:{origin.id}",
            identifier=record_id,
            defaults={"forgotten_at": timezone.now(), "reason": REASON},
        )
        record.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("memory", "0012_omp_exchange"),
        ("conversations", "0092_message_memory_write_data"),
    ]

    operations = [
        migrations.RunPython(erase_previously_forgotten, migrations.RunPython.noop),
    ]
