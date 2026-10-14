from django.db import migrations

# Pricing and capabilities from the official provider docs:
# https://platform.claude.com/docs/en/models/haiku-5-5/overview
# https://platform.claude.com/docs/en/models/sonnet-5-5/overview
# https://developers.openai.com/api/docs/models/gpt-6.1-sol
#
# Claude Haiku 5.5 and Sonnet 5.5 think adaptively by default and reject
# temperature and an explicit disabled thinking config. Haiku 5.5's API
# default effort is medium; Sonnet 5.5 keeps high. Haiku 5.5 bills 5x for
# prompts over 100K tokens; DARE has one rate per model, so it stores the
# under-100K tier.
#
# GPT-6.1 Sol follows the gpt-6 rows: a reasoning model without temperature,
# and supports_effort stays False because DARE's effort plumbing emits
# Anthropic-shaped `output_config`.
NEW_LLM_DATA = [
    {
        "name": "Claude Haiku 5.5",
        "identifier": "claude-haiku-5-5",
        "provider": "claude",
        "description": "Anthropic's fastest model for high-volume, everyday tasks.",
        "tier": "flash",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "medium",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "0.10",
        "output_token_rate_per_million": "0.50",
        "cached_input_token_rate_per_million": "0.0100",
    },
    {
        "name": "Claude Sonnet 5.5",
        "identifier": "claude-sonnet-5-5",
        "provider": "claude",
        "description": "Anthropic's balance of speed and intelligence for everyday work.",
        "tier": "advanced",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "high",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "2.00",
        "output_token_rate_per_million": "10.00",
        "cached_input_token_rate_per_million": "0.1000",
    },
    {
        "name": "GPT-6.1 Sol",
        "identifier": "gpt-6.1-sol",
        "provider": "openai",
        "description": "OpenAI's GPT-6.1 balanced model, near-Astra quality at Sol pricing.",
        "tier": "advanced",
        "is_reasoning": True,
        "reasoning_level": "cost_predictable",
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "2.00",
        "output_token_rate_per_million": "10.00",
        "cached_input_token_rate_per_million": "0.1000",
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
        "\nHaiku 5.5 / Sonnet 5.5 / GPT-6.1 Sol Seed Migration: "
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
        ("conversations", "0107_socratic_conversations_full_history"),
    ]

    operations = [
        migrations.RunPython(seed_new_models, reverse_seed_new_models),
    ]
