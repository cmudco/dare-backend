from django.db import migrations, models


def classify_existing_llms(apps, schema_editor):
    """Auto-classify existing LLMs into tiers based on their token pricing."""
    LLM = apps.get_model("conversations", "LLM")
    for llm in LLM.objects.all():
        input_rate = float(llm.input_token_rate_per_million)
        output_rate = float(llm.output_token_rate_per_million)

        if input_rate >= 10.0 or output_rate >= 30.0:
            llm.tier = "premium"
        elif (input_rate <= 1.0 and output_rate <= 4.0) and (input_rate > 0 or output_rate > 0):
            llm.tier = "flash"
        else:
            llm.tier = "advanced"
        llm.save(update_fields=["tier"])


def reverse_classify(apps, schema_editor):
    """Reset all tiers to advanced."""
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.all().update(tier="advanced")


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0061_add_file_owner_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="llm",
            name="tier",
            field=models.CharField(
                choices=[
                    ("premium", "Premium"),
                    ("advanced", "Advanced"),
                    ("flash", "Flash"),
                ],
                default="advanced",
                help_text=(
                    "Cost/capability tier for grouping models in the UI. "
                    "Premium: Flagship models (e.g., Claude Opus, GPT-4.5). "
                    "Advanced: Mid-range models (e.g., Claude Sonnet, GPT-4o, Gemini Pro). "
                    "Flash: Fast, cost-optimized models (e.g., Claude Haiku, GPT-4o-mini, Gemini Flash)."
                ),
                max_length=20,
            ),
        ),
        migrations.RunPython(classify_existing_llms, reverse_classify),
    ]
