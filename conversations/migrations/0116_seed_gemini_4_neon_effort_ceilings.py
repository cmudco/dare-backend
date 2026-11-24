from django.db import migrations

# Gemini 4 Neon: https://aistudio.google.com/docs/models
# https://aistudio.google.com/docs/pricing
#
# Effort ceilings were measured against the Gemini API on 2026-11-24: Neon and
# Hydrogen accept the XHIGH thinking level, Carbon and Argon reject it with a
# 400, and no Gemini model accepts anything above XHIGH.
NEON = {
    "name": "Gemini 4 Neon",
    "identifier": "gemini-4-neon",
    "provider": "gemini",
    "description": "Google's Gemini 4 model for demanding coding and research work.",
    "tier": "premium",
    "is_reasoning": True,
    "reasoning_level": "cost_unconstrained",
    "supports_vision": True,
    "supports_temperature": True,
    "supports_effort": True,
    "default_effort": "high",
    "max_effort": "xhigh",
    "input_token_rate_per_million": "3.00",
    "output_token_rate_per_million": "15.00",
    "cached_input_token_rate_per_million": "0.1500",
}

XHIGH_MODELS = ("gemini-4-neon", "gemini-4-hydrogen")


def seed(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.get_or_create(
        identifier=NEON["identifier"],
        defaults={k: v for k, v in NEON.items() if k != "identifier"},
    )
    gemini = LLM.objects.filter(provider="gemini")
    gemini.exclude(identifier__in=XHIGH_MODELS).update(max_effort="high")
    gemini.filter(identifier__in=XHIGH_MODELS).update(max_effort="xhigh")


def unseed(apps, schema_editor):
    """Remove Neon only when no application record references it."""
    LLM = apps.get_model("conversations", "LLM")
    llm = LLM.objects.filter(identifier=NEON["identifier"]).first()
    if llm is None:
        return
    for model in apps.get_models():
        fields = [*model._meta.local_fields, *model._meta.local_many_to_many]
        for field in fields:
            if getattr(field.remote_field, "model", None) is not LLM:
                continue
            if model._base_manager.filter(**{field.name: llm.pk}).exists():
                return
    llm.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0115_llm_max_effort"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
