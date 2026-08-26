from django.db import migrations
from django.db.models import Count, Sum


def backfill(apps, schema_editor):
    """Seed the counter from transactions already on record.

    The counter is maintained incrementally from here on, so this aggregate
    runs once rather than on every wallet read.
    """
    Message = apps.get_model("conversations", "Message")
    LiteLLMSpend = apps.get_model("billing", "LiteLLMSpend")

    # Sourced from Message rather than Transaction: only Message carries the
    # key it was routed through, and its cost is the same reference figure.
    rows = (
        Message._base_manager.filter(
            litellm_key__isnull=False,
            cost__isnull=False,
            cost__gt=0,
        )
        .values("conversation__user_id", "litellm_key_id")
        .annotate(total=Sum("cost"), calls=Count("id"))
    )

    LiteLLMSpend._base_manager.bulk_create(
        [
            LiteLLMSpend(
                user_id=row["conversation__user_id"],
                litellm_key_id=row["litellm_key_id"],
                total_reference_amount=row["total"],
                call_count=row["calls"],
            )
            for row in rows
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0023_litellmspend"),
        ("conversations", "0070_message_litellm_audit"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
