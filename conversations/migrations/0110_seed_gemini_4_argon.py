from django.db import migrations

# Pricing and capabilities from the official Gemini docs:
# https://ai.google.dev/gemini-api/docs/pricing
# https://ai.google.dev/gemini-api/docs/models/gemini-4-argon
#
# Gemini 4 Argon (GA 2026-10-13) is Google's flagship reasoning model, with
# text/image/video/audio/PDF input and low/medium/high thinking levels.
# Its rates are introductory and expire 2026-11-30, after which input/output
# double to 4.00 / 20.00 and cached input to 0.20 -- revisit then.
GEMINI_4_ARGON = {
    "name": "Gemini 4 Argon",
    "identifier": "gemini-4-argon",
    "provider": "gemini",
    "description": "Google's flagship multimodal reasoning model.",
    "tier": "premium",
    "is_reasoning": True,
    "reasoning_level": "cost_unconstrained",
    "supports_vision": True,
    "supports_temperature": True,
    "supports_effort": True,
    "default_effort": "high",
    "input_token_rate_per_million": "2.00",
    "output_token_rate_per_million": "10.00",
    "cached_input_token_rate_per_million": "0.1000",
}

RATE_FIELDS = (
    "input_token_rate_per_million",
    "output_token_rate_per_million",
    "cached_input_token_rate_per_million",
)


def seed_gemini_4_argon(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")

    _, created = LLM.objects.get_or_create(
        identifier=GEMINI_4_ARGON["identifier"],
        defaults={k: v for k, v in GEMINI_4_ARGON.items() if k != "identifier"},
    )
    if not created:
        LLM.objects.filter(identifier=GEMINI_4_ARGON["identifier"]).update(
            **{field: GEMINI_4_ARGON[field] for field in RATE_FIELDS}
        )

    print(
        "\nGemini 4 Argon Seed Migration: "
        f"{'Created' if created else 'Updated existing row'}\n"
    )


def reverse_seed_gemini_4_argon(apps, schema_editor):
    """Remove the seeded row only when no application records reference it."""
    LLM = apps.get_model("conversations", "LLM")

    try:
        llm = LLM.objects.get(identifier=GEMINI_4_ARGON["identifier"])
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
        ("conversations", "0109_alter_artifact_artifact_type"),
    ]

    operations = [
        migrations.RunPython(seed_gemini_4_argon, reverse_seed_gemini_4_argon),
    ]
