"""
Add Message.llm_name and recover it for replies whose model was already
deleted. The reply's billing transaction kept the name; it is matched by user,
identical token counts and a ten-minute window, and used only when every
candidate names the same model. Replies with no such match stay unnamed.
"""

from datetime import timedelta

from django.db import migrations, models

AI_ASSISTANT = 2
MATCH_WINDOW = timedelta(minutes=10)


def recover_deleted_model_names(apps, schema_editor):
    Message = apps.get_model("conversations", "Message")
    Transaction = apps.get_model("billing", "Transaction")

    orphans = (
        Message._base_manager.filter(
            llm__isnull=True,
            llm_name__isnull=True,
            sender_type=AI_ASSISTANT,
            output_tokens__gt=0,
        )
        .filter(
            models.Q(litellm_model_name__isnull=True) | models.Q(litellm_model_name="")
        )
        .values_list(
            "id",
            "conversation__user_id",
            "input_tokens",
            "output_tokens",
            "created_at",
        )
    )

    recovered = unmatched = ambiguous = 0
    for (
        message_id,
        user_id,
        input_tokens,
        output_tokens,
        created_at,
    ) in orphans.iterator():
        names = set(
            Transaction._base_manager.filter(
                user_id=user_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                created_at__range=(
                    created_at - MATCH_WINDOW,
                    created_at + MATCH_WINDOW,
                ),
            )
            .exclude(llm_name__isnull=True)
            .exclude(llm_name="")
            .values_list("llm_name", flat=True)
        )
        if len(names) == 1:
            Message._base_manager.filter(pk=message_id).update(llm_name=names.pop())
            recovered += 1
        elif names:
            ambiguous += 1
        else:
            unmatched += 1

    print(
        f"\n  Deleted-model names: {recovered} recovered, "
        f"{ambiguous} ambiguous, {unmatched} unmatched"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0105_backfill_energy_new_models"),
        ("billing", "0028_spend_caps_and_gateway_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="llm_name",
            field=models.CharField(
                blank=True,
                help_text="Name of the LLM, recorded when the model is deleted so history keeps its label.",
                max_length=255,
                null=True,
            ),
        ),
        migrations.RunPython(recover_deleted_model_names, migrations.RunPython.noop),
    ]
