from django.db import migrations


# Pricing and capabilities from the official Anthropic docs
# (https://platform.claude.com/docs/en/docs/about-claude/pricing and
# the Fable 5 launch notes): $10 / MTok input, $50 / MTok output.
# Adaptive thinking is always on for Fable 5 and temperature is not
# supported; effort controls thinking depth (default high).
FABLE_LLM_DATA = [
    {
        "name": "Claude Fable 5",
        "identifier": "claude-fable-5",
        "provider": "claude",
        "supports_vision": True,
        "tier": "premium",
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "high",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "10.00",
        "output_token_rate_per_million": "50.00",
    },
]


def seed_claude_fable_5(apps, schema_editor):
    """
    Seed the Claude Fable 5 chat model. Existing rows with the same
    identifier are left untouched except for token rates, which are
    refreshed to the documented values.
    """
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in FABLE_LLM_DATA:
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

    for llm in FABLE_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
        )

    print(
        "\nClaude Fable 5 Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_claude_fable_5(apps, schema_editor):
    """
    Remove only unreferenced rows from this seed list.
    """
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in FABLE_LLM_DATA:
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
        ("conversations", "0075_seed_modern_llm_models"),
    ]

    operations = [
        migrations.RunPython(seed_claude_fable_5, reverse_seed_claude_fable_5),
    ]
