from django.db import migrations

# Pricing and capabilities from the official Anthropic model and pricing docs:
# https://platform.claude.com/docs/en/about-claude/models/overview
# https://platform.claude.com/docs/en/about-claude/pricing
# Claude Opus 5 uses adaptive thinking, supports effort controls, and does not
# accept manual extended-thinking configuration. The Claude API defaults effort
# to high. Tier "premium" matches the existing Opus model rows.
OPUS_LLM_DATA = [
    {
        "name": "Claude Opus 5",
        "identifier": "claude-opus-5",
        "provider": "claude",
        "supports_vision": True,
        "tier": "premium",
        "supports_temperature": False,
        "supports_effort": True,
        "supports_adaptive_thinking": True,
        "default_effort": "high",
        "default_adaptive_thinking_enabled": True,
        "input_token_rate_per_million": "5.00",
        "output_token_rate_per_million": "25.00",
    },
]


def seed_claude_opus_5(apps, schema_editor):
    """Seed Claude Opus 5 and refresh its documented token rates."""
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in OPUS_LLM_DATA:
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

    for llm in OPUS_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
        )

    print(
        "\nClaude Opus 5 Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_claude_opus_5(apps, schema_editor):
    """Remove the seeded model only when no application records reference it."""
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in OPUS_LLM_DATA:
        try:
            llm = LLM.objects.get(identifier=llm_data["identifier"])
        except LLM.DoesNotExist:
            continue

        is_referenced = False
        for model in apps.get_models():
            for field in model._meta.local_fields:
                remote_model = getattr(field.remote_field, "model", None)
                if remote_model is not LLM:
                    continue
                if model._base_manager.filter(**{field.attname: llm.pk}).exists():
                    is_referenced = True
                    break
            for field in model._meta.local_many_to_many:
                remote_model = getattr(field.remote_field, "model", None)
                if remote_model is not LLM:
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
        ("conversations", "0085_sonnet5_default_effort_medium"),
    ]

    operations = [
        migrations.RunPython(seed_claude_opus_5, reverse_seed_claude_opus_5),
    ]
