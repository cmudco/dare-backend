from django.db import migrations

# Pricing and capabilities from the official provider docs:
# https://developers.openai.com/api/docs/pricing
# https://developers.openai.com/api/docs/models/gpt-6-astra
# https://platform.claude.com/docs/en/about-claude/pricing
#
# GPT-6 (Astra / Sol / Luna) are reasoning models with text + image input, so
# temperature is not accepted. supports_effort stays False for the same reason
# as the gpt-5.6 rows: DARE's effort plumbing emits Anthropic-shaped
# `output_config`, which OpenAI would reject.
#
# Claude Opus 5.5 and Fable 5.1 always think (an explicit disabled config is a
# 400) and reject temperature. Opus 5.5's API default effort is medium, one
# level below the rest of the Claude family.
NEW_LLM_DATA = [
    {
        "name": "GPT-6 Astra",
        "identifier": "gpt-6-astra",
        "provider": "openai",
        "description": "OpenAI's GPT-6 flagship for the hardest reasoning and agentic work.",
        "tier": "premium",
        "is_reasoning": True,
        "reasoning_level": "cost_unconstrained",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "10.00",
        "output_token_rate_per_million": "50.00",
        "cached_input_token_rate_per_million": "1.0000",
    },
    {
        "name": "GPT-6 Sol",
        "identifier": "gpt-6-sol",
        "provider": "openai",
        "description": "OpenAI's balanced GPT-6 model for production reasoning workloads.",
        "tier": "advanced",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "2.00",
        "output_token_rate_per_million": "10.00",
        "cached_input_token_rate_per_million": "0.2000",
    },
    {
        "name": "GPT-6 Luna",
        "identifier": "gpt-6-luna",
        "provider": "openai",
        "description": "OpenAI's cost-optimized GPT-6 model for high-volume tasks.",
        "tier": "flash",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "0.10",
        "output_token_rate_per_million": "0.50",
        "cached_input_token_rate_per_million": "0.0100",
    },
    {
        "name": "Claude Opus 5.5",
        "identifier": "claude-opus-5-5",
        "provider": "claude",
        "description": "Anthropic's latest Opus model with always-on adaptive thinking.",
        "tier": "premium",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "medium",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "4.00",
        "output_token_rate_per_million": "20.00",
        "cached_input_token_rate_per_million": "0.2000",
    },
    {
        "name": "Claude Fable 5.1",
        "identifier": "claude-fable-5-1",
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
        "input_token_rate_per_million": "10.00",
        "output_token_rate_per_million": "50.00",
        "cached_input_token_rate_per_million": "0.2500",
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
        "\nGPT-6 / Opus 5.5 / Fable 5.1 Seed Migration: "
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
        ("conversations", "0101_ensemble_preset"),
    ]

    operations = [
        migrations.RunPython(seed_new_models, reverse_seed_new_models),
    ]
