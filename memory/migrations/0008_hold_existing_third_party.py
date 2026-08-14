"""Hold every third-party fact that predates the gate rule.

The gate now files ``sensitivity=third-party`` as held — the person consenting
is not the person the fact is about — but rows written before the rule are
still active and ordinary retrieval returns them. Red-teamed twice: the fix
passed on new writes while an unrelated doctor's address and birth date from
the earlier run kept coming back.

Forward only. Releasing a third-party fact is a decision a person makes from
the hold queue, not something a rollback should do in bulk.
"""

from django.db import migrations


def hold_third_party(apps, schema_editor):
    MemoryRecord = apps.get_model("memory", "MemoryRecord")
    MemoryLedgerEntry = apps.get_model("memory", "MemoryLedgerEntry")

    rows = MemoryRecord.objects.filter(sensitivity="third-party", state="active")
    for record in rows:
        record.state = "held"
        record.save(update_fields=["state", "updated_at"])
        MemoryLedgerEntry.objects.create(
            user_id=record.user_id,
            action="hold",
            proposed_action="hold",
            reason=(
                "Third-party facts are held, and this one predates the rule: "
                "the person consenting is not the person the fact is about."
            ),
            applied=True,
            record=record,
            detail=record.text,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0007_embed_rules_by_situation"),
    ]

    operations = [
        migrations.RunPython(hold_third_party, migrations.RunPython.noop),
    ]
