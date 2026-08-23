from django.db import migrations


# Pricing and capabilities from the official OpenAI docs
# (https://developers.openai.com/api/docs/pricing and the GPT-5.6 launch notes,
# released 2026-07-09). The family is three tiers over one architecture:
# Sol (frontier), Terra (balanced production), Luna (cost-optimized volume).
# All three take text + image input, emit text, and are reasoning models, so
# temperature is not accepted on Chat Completions - same as the gpt-5.5 row.
# supports_effort stays False: DARE's effort plumbing emits Anthropic-shaped
# `output_config`, which OpenAI would reject. The bare `gpt-5.6` alias routes to
# Sol upstream, so it is not seeded as a separate row.
GPT_5_6_LLM_DATA = [
    {
        "name": "GPT-5.6 Sol",
        "identifier": "gpt-5.6-sol",
        "provider": "openai",
        "tier": "premium",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "5.00",
        "output_token_rate_per_million": "30.00",
    },
    {
        "name": "GPT-5.6 Terra",
        "identifier": "gpt-5.6-terra",
        "provider": "openai",
        "tier": "advanced",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "2.50",
        "output_token_rate_per_million": "15.00",
    },
    {
        "name": "GPT-5.6 Luna",
        "identifier": "gpt-5.6-luna",
        "provider": "openai",
        "tier": "flash",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": False,
        "supports_effort": False,
        "supports_adaptive_thinking": False,
        "input_token_rate_per_million": "1.00",
        "output_token_rate_per_million": "6.00",
    },
]


def seed_gpt_5_6_family(apps, schema_editor):
    """
    Seed the GPT-5.6 chat models. Existing rows with the same identifier are
    left untouched except for token rates, which are refreshed to the
    documented values.
    """
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in GPT_5_6_LLM_DATA:
        _, created = LLM.objects.get_or_create(
            identifier=llm["identifier"],
            defaults={
                "name": llm["name"],
                "provider": llm["provider"],
                "is_active": llm.get("is_active", True),
                "is_reasoning": llm.get("is_reasoning", False),
                "supports_vision": llm.get("supports_vision", True),
                "supports_temperature": llm.get("supports_temperature", True),
                "supports_effort": llm.get("supports_effort", False),
                "supports_adaptive_thinking": llm.get(
                    "supports_adaptive_thinking", False
                ),
                "default_effort": llm.get("default_effort", "high"),
                "default_adaptive_thinking_enabled": llm.get(
                    "default_adaptive_thinking_enabled", False
                ),
                "is_image_generator": llm.get("is_image_generator", False),
                "is_audio_transcriber": llm.get("is_audio_transcriber", False),
                "tier": llm.get("tier", "advanced"),
                "input_token_rate_per_million": llm.get(
                    "input_token_rate_per_million", "0.00"
                ),
                "output_token_rate_per_million": llm.get(
                    "output_token_rate_per_million", "0.00"
                ),
            },
        )
        if created:
            created_count += 1
        else:
            skipped_count += 1

    for llm in GPT_5_6_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
        )

    print(
        "\nGPT-5.6 Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_gpt_5_6_family(apps, schema_editor):
    """
    Remove only unreferenced rows from this seed list.
    """
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in GPT_5_6_LLM_DATA:
        try:
            llm = LLM.objects.get(identifier=llm_data["identifier"])
        except LLM.DoesNotExist:
            continue

        if (
            not llm.conversations_using_model.exists()
            and not llm.messages.exists()
            and not llm.model_groups.exists()
        ):
            llm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0077_seed_claude_sonnet_5"),
    ]

    operations = [
        migrations.RunPython(seed_gpt_5_6_family, reverse_seed_gpt_5_6_family),
    ]
