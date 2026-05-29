from django.db import migrations, models


EFFORT_CHOICES = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("xhigh", "Extra High"),
    ("max", "Max"),
]


def seed_model_capabilities(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")

    LLM.objects.filter(is_reasoning=True).update(supports_temperature=False)

    for version in ("claude-opus-4-7", "claude-opus-4-8"):
        LLM.objects.filter(provider="claude", identifier__icontains=version).update(
            supports_temperature=False,
            supports_effort=True,
            supports_adaptive_thinking=True,
            default_effort="high",
            default_adaptive_thinking_enabled=True,
        )


def reverse_model_capabilities(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.update(
        supports_temperature=True,
        supports_effort=False,
        supports_adaptive_thinking=False,
        default_effort="high",
        default_adaptive_thinking_enabled=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0073_merge_pptx_artifact_type_and_tool_call_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="llm",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text="Whether this model is available for new selections.",
            ),
        ),
        migrations.AddField(
            model_name="llm",
            name="supports_temperature",
            field=models.BooleanField(
                default=True,
                help_text="Whether this model accepts the temperature sampling parameter.",
            ),
        ),
        migrations.AddField(
            model_name="llm",
            name="supports_effort",
            field=models.BooleanField(
                default=False,
                help_text="Whether this model accepts an effort control parameter.",
            ),
        ),
        migrations.AddField(
            model_name="llm",
            name="supports_adaptive_thinking",
            field=models.BooleanField(
                default=False,
                help_text="Whether this model supports provider-native adaptive thinking.",
            ),
        ),
        migrations.AddField(
            model_name="llm",
            name="default_effort",
            field=models.CharField(
                choices=EFFORT_CHOICES,
                default="high",
                help_text="Default effort level when the conversation has no explicit effort override.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="llm",
            name="default_adaptive_thinking_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Whether adaptive thinking should be sent by default for this model.",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="effort",
            field=models.CharField(
                blank=True,
                choices=EFFORT_CHOICES,
                help_text="Optional effort override for models that support effort. Null uses the selected model default.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(seed_model_capabilities, reverse_model_capabilities),
    ]
