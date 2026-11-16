from django.db import migrations

# Pricing and capabilities from the official provider docs:
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/models/gpt-6.1-terra
# https://aistudio.google.com/docs/models
# https://aistudio.google.com/docs/pricing
#
# Claude Fable 5.2 succeeds Fable 5.1 at a lower price; thinking is always on
# and it rejects temperature, so it mirrors the Fable 5.1 row.
#
# GPT-6.1 Terra follows the gpt-6 rows: a reasoning model without temperature,
# and supports_effort stays False because DARE's effort plumbing emits
# Anthropic-shaped `output_config`.
#
# Gemini 4 Carbon is Google's most capable Gemini 4 model; its rates are
# introductory and double on 2026-12-01 (4.00 / 20.00, cached 0.20), same as
# Gemini 4 Argon -- revisit then. Gemini 4 Hydrogen is the cheaper everyday
# Gemini 4 model; at twice Gemini 3.8 Flash's price it sits in "advanced".
# Hydrogen advertises an xhigh thinking level, but google-genai only offers
# up to HIGH, so GeminiService keeps clamping xhigh to high.
NEW_LLM_DATA = [
    {
        "name": "Claude Fable 5.2",
        "identifier": "claude-fable-5-2",
        "provider": "claude",
        "description": "Anthropic's most capable model for demanding reasoning and long-horizon work.",
        "tier": "premium",
        "is_reasoning": True,
        "reasoning_level": "cost_unconstrained",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "high",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "8.00",
        "output_token_rate_per_million": "40.00",
        "cached_input_token_rate_per_million": "0.2000",
    },
    {
        "name": "GPT-6.1 Terra",
        "identifier": "gpt-6.1-terra",
        "provider": "openai",
        "description": "OpenAI's fast, efficient GPT-6.1 model for visual reasoning and writing.",
        "tier": "advanced",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "1.00",
        "output_token_rate_per_million": "5.00",
        "cached_input_token_rate_per_million": "0.0500",
    },
    {
        "name": "Gemini 4 Carbon",
        "identifier": "gemini-4-carbon",
        "provider": "gemini",
        "description": "Google's most capable Gemini 4 reasoning model.",
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
    },
    {
        "name": "Gemini 4 Hydrogen",
        "identifier": "gemini-4-hydrogen",
        "provider": "gemini",
        "description": "Google's fast, efficient Gemini 4 model for everyday coding and agentic work.",
        "tier": "advanced",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": True,
        "supports_effort": True,
        "default_effort": "high",
        "input_token_rate_per_million": "1.50",
        "output_token_rate_per_million": "7.50",
        "cached_input_token_rate_per_million": "0.0750",
    },
]

RATE_FIELDS = (
    "input_token_rate_per_million",
    "output_token_rate_per_million",
    "cached_input_token_rate_per_million",
)


def seed_new_models(apps, schema_editor):
    """Seed the new models; existing rows only get their rates refreshed."""
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    for llm in NEW_LLM_DATA:
        _, created = LLM.objects.get_or_create(
            identifier=llm["identifier"],
            defaults={k: v for k, v in llm.items() if k != "identifier"},
        )
        if created:
            created_count += 1
        else:
            LLM.objects.filter(identifier=llm["identifier"]).update(
                **{field: llm[field] for field in RATE_FIELDS}
            )

    print(
        "\nFable 5.2 / GPT-6.1 Terra / Gemini 4 Carbon + Hydrogen Seed Migration: "
        f"Created {created_count}, Updated {len(NEW_LLM_DATA) - created_count}\n"
    )


def reverse_seed_new_models(apps, schema_editor):
    """Remove seeded rows only when no application records reference them."""
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in NEW_LLM_DATA:
        try:
            llm = LLM.objects.get(identifier=llm_data["identifier"])
        except LLM.DoesNotExist:
            continue

        is_referenced = False
        for model in apps.get_models():
            for field in model._meta.local_fields:
                if getattr(field.remote_field, "model", None) is not LLM:
                    continue
                if model._base_manager.filter(**{field.attname: llm.pk}).exists():
                    is_referenced = True
                    break
            for field in model._meta.local_many_to_many:
                if getattr(field.remote_field, "model", None) is not LLM:
                    continue
                if model._base_manager.filter(**{field.name: llm.pk}).exists():
                    is_referenced = True
                    break
            if is_referenced:
                break

        if not is_referenced:
            llm.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0113_seed_gpt_image_models"),
    ]

    operations = [
        migrations.RunPython(seed_new_models, reverse_seed_new_models),
    ]
