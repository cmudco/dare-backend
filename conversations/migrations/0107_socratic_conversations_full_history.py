from django.db import migrations


def record_full_history(apps, schema_editor):
    Conversation = apps.get_model("conversations", "Conversation")
    Conversation._base_manager.filter(source="SocraticBots").update(history_limit=0)


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0106_message_llm_name"),
    ]

    operations = [
        migrations.RunPython(record_full_history, migrations.RunPython.noop),
    ]
