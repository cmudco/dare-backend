from django.db import migrations

# Pricing and capabilities from the official Gemini API docs and model cards:
# https://ai.google.dev/gemini-api/docs/pricing
# https://deepmind.google/models/model-cards/gemini-3-7-flash/
#
# Gemini 3.7 Flash (2026-08-13) is the current workhorse Flash model and
# supersedes 3.6 Flash. Its listed rates are introductory and expire
# 2026-12-31, after which they double to 1.50 / 7.50 -- revisit then.
# Gemini 3.5 Flash-Lite (2026-07-21) is the newest Flash-Lite; Google did not
# ship a 3.6 or 3.7 Flash-Lite. 3.1 Flash-Lite stays active alongside it.
#
# 3.7 Flash exposes LOW/MEDIUM/HIGH thinking levels with MEDIUM as the API
# default, which GeminiService now forwards as a ThinkingConfig, so the effort
# control is wired end to end for this row.
GEMINI_LLM_DATA = [
    {
        "name": "Gemini 3.7 Flash",
        "identifier": "gemini-3.7-flash",
        "provider": "gemini",
        "supports_vision": True,
        "tier": "flash",
        "is_reasoning": True,
        "supports_effort": True,
        "default_effort": "medium",
        "input_token_rate_per_million": "0.75",
        "output_token_rate_per_million": "3.75",
    },
    {
        "name": "Gemini 3.5 Flash-Lite",
        "identifier": "gemini-3.5-flash-lite",
        "provider": "gemini",
        "supports_vision": True,
        "tier": "flash",
        "input_token_rate_per_million": "0.30",
        "output_token_rate_per_million": "2.50",
    },
]


def seed_gemini_3_7_flash(apps, schema_editor):
    """Seed the current Gemini Flash rows and refresh their documented rates."""
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in GEMINI_LLM_DATA:
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

    for llm in GEMINI_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
            is_reasoning=llm.get("is_reasoning", False),
            supports_effort=llm.get("supports_effort", False),
            default_effort=llm.get("default_effort", "high"),
        )

    print(
        "\nGemini 3.7 Flash Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_gemini_3_7_flash(apps, schema_editor):
    """Remove the seeded rows only when no application records reference them."""
    LLM = apps.get_model("conversations", "LLM")

    for llm_data in GEMINI_LLM_DATA:
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
        ("conversations", "0094_update_gpt_5_6_rates"),
    ]

    operations = [
        migrations.RunPython(seed_gemini_3_7_flash, reverse_seed_gemini_3_7_flash),
    ]
