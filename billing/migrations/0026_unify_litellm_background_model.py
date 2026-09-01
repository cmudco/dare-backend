from django.db import migrations, models


def copy_background_model(apps, schema_editor):
    LiteLLMKey = apps.get_model("billing", "LiteLLMKey")
    for key in LiteLLMKey.objects.all().iterator():
        key.background_model = key.memory_model or key.title_model or ""
        key.save(update_fields=["background_model"])


def restore_legacy_models(apps, schema_editor):
    LiteLLMKey = apps.get_model("billing", "LiteLLMKey")
    for key in LiteLLMKey.objects.all().iterator():
        key.memory_model = key.background_model
        key.title_model = key.background_model
        key.save(update_fields=["memory_model", "title_model"])


class Migration(migrations.Migration):
    dependencies = [("billing", "0025_merge_20260825_1516")]

    operations = [
        migrations.AddField(
            model_name="litellmkey",
            name="background_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Model this proxy serves for conversation titles, summaries, "
                    "memory extraction, and retrieval query analysis. Blank falls "
                    "back to DARE's configured background model."
                ),
                max_length=255,
                verbose_name="Background Model",
            ),
        ),
        migrations.RunPython(copy_background_model, restore_legacy_models),
        migrations.RemoveField(model_name="litellmkey", name="memory_model"),
        migrations.RemoveField(model_name="litellmkey", name="title_model"),
    ]
