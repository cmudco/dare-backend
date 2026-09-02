from django.db import migrations

# Pricing and capabilities from the official Gemini docs and model guide:
# https://ai.google.dev/gemini-api/docs/pricing
# https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash
#
# Gemini 3.8 Flash (2026-09-02) supersedes 3.7 Flash as the workhorse Flash
# model: better accuracy at higher token consumption, with the same
# thinking_level effort control (minimal/low/medium/high, MEDIUM default).
# Its rates are introductory and expire 2026-12-31, after which input/output
# double to 1.50 / 7.50 and cached input to 0.15 -- revisit then.
#
# 3.7 Flash stays active so existing conversations keep resolving their model.
GEMINI_3_8_FLASH = {
    "name": "Gemini 3.8 Flash",
    "identifier": "gemini-3.8-flash",
    "provider": "gemini",
    "description": "Google's fast multimodal model with configurable thinking levels.",
    "tier": "flash",
    "is_reasoning": True,
    "reasoning_level": "cost_predictable",
    "supports_vision": True,
    "supports_temperature": True,
    "supports_effort": True,
    "default_effort": "medium",
    "input_token_rate_per_million": "0.75",
    "output_token_rate_per_million": "3.75",
    "cached_input_token_rate_per_million": "0.0750",
}


def seed_gemini_3_8_flash(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")

    _, created = LLM.objects.get_or_create(
        identifier=GEMINI_3_8_FLASH["identifier"],
        defaults={
            key: value
            for key, value in GEMINI_3_8_FLASH.items()
            if key != "identifier"
        },
    )

    if not created:
        LLM.objects.filter(identifier=GEMINI_3_8_FLASH["identifier"]).update(
            input_token_rate_per_million=GEMINI_3_8_FLASH[
                "input_token_rate_per_million"
            ],
            output_token_rate_per_million=GEMINI_3_8_FLASH[
                "output_token_rate_per_million"
            ],
            cached_input_token_rate_per_million=GEMINI_3_8_FLASH[
                "cached_input_token_rate_per_million"
            ],
            is_reasoning=GEMINI_3_8_FLASH["is_reasoning"],
            reasoning_level=GEMINI_3_8_FLASH["reasoning_level"],
            supports_effort=GEMINI_3_8_FLASH["supports_effort"],
            default_effort=GEMINI_3_8_FLASH["default_effort"],
        )

    print(
        "\nGemini 3.8 Flash Seed Migration: "
        f"{'Created' if created else 'Updated existing row'}\n"
    )


def reverse_seed_gemini_3_8_flash(apps, schema_editor):
    """Remove the seeded row only when no application records reference it."""
    LLM = apps.get_model("conversations", "LLM")

    try:
        llm = LLM.objects.get(identifier=GEMINI_3_8_FLASH["identifier"])
    except LLM.DoesNotExist:
        return

    for model in apps.get_models():
        for field in model._meta.local_fields:
            if getattr(field.remote_field, "model", None) is not LLM:
                continue
            if model._base_manager.filter(**{field.attname: llm.pk}).exists():
                return
        for field in model._meta.local_many_to_many:
            if getattr(field.remote_field, "model", None) is not LLM:
                continue
            if model._base_manager.filter(**{field.name: llm.pk}).exists():
                return

    llm.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0098_update_gpt_5_6_sol_rates"),
    ]

    operations = [
        migrations.RunPython(seed_gemini_3_8_flash, reverse_seed_gemini_3_8_flash),
    ]
