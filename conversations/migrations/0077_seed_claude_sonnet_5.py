from django.db import migrations


# Pricing and capabilities from the official Anthropic docs
# (https://platform.claude.com/docs/en/about-claude/models/overview and the
# Claude Sonnet 5 launch notes, released 2026-06-30): list price is
# $3 / MTok input, $15 / MTok output. Introductory pricing of $2 / $10 per MTok
# applies through 2026-08-31, after which it reverts to the list price seeded
# here; we track the durable rate-card price so this migration does not need a
# follow-up when the promo ends. Sonnet 5 is the modern adaptive-thinking family
# (like Opus 4.7/4.8 and Fable 5): adaptive thinking is supported, temperature is
# not, and effort controls thinking depth (default high). Tier "advanced" matches
# the other Sonnet rows.
SONNET_LLM_DATA = [
    {
        "name": "Claude Sonnet 5",
        "identifier": "claude-sonnet-5",
        "provider": "claude",
        "supports_vision": True,
        "tier": "advanced",
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "high",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "3.00",
        "output_token_rate_per_million": "15.00",
    },
]


def seed_claude_sonnet_5(apps, schema_editor):
    """
    Seed the Claude Sonnet 5 chat model. Existing rows with the same
    identifier are left untouched except for token rates, which are
    refreshed to the documented values.
    """
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in SONNET_LLM_DATA:
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

    for llm in SONNET_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
        )

    print(
        "\nClaude Sonnet 5 Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_claude_sonnet_5(apps, schema_editor):
    """
    Remove only unreferenced rows from this seed list.
    """
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in SONNET_LLM_DATA:
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
        ("conversations", "0076_seed_claude_fable_5"),
    ]

    operations = [
        migrations.RunPython(seed_claude_sonnet_5, reverse_seed_claude_sonnet_5),
    ]
