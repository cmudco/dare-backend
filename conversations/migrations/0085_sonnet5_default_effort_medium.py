from django.db import migrations

MODEL_IDENTIFIER = "claude-sonnet-5"


def use_medium_effort(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.filter(identifier=MODEL_IDENTIFIER).update(default_effort="medium")


def restore_high_effort(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.filter(identifier=MODEL_IDENTIFIER).update(default_effort="high")


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0084_message_context_trace"),
    ]

    operations = [
        migrations.RunPython(use_medium_effort, restore_high_effort),
    ]
