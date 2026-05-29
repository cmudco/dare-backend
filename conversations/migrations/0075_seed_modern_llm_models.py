from django.db import migrations


OPENAI_MAX_COMPLETION_TOKEN_MODELS = [
    "gpt-5",
    "gpt-5.1-2025-11-13",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
]

OPENAI_UNSUPPORTED_TEMPERATURE_MODELS = [
    "gpt-5",
    "gpt-5.5",
]

DEPRECATED_GEMINI_IDENTIFIERS = [
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
]

NON_PICKER_GEMINI_IDENTIFIERS = [
    "gemini-3.1-pro-preview-customtools",
]

REVERSE_ONLY_IDENTIFIERS = [
    # Removed from this seed after replacing the stale Gemini 3 preview entry
    # with the documented Gemini 3.5 and Gemini 3.1 model IDs.
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview-customtools",
]

MODERN_LLM_DATA = [
    {
        "name": "Gemini 3.1 Pro Preview",
        "identifier": "gemini-3.1-pro-preview",
        "provider": "gemini",
        "supports_vision": True,
        "input_token_rate_per_million": "2.00",
        "output_token_rate_per_million": "12.00",
        "tier": "premium",
    },
    {
        "name": "Gemini 3.1 Flash-Lite",
        "identifier": "gemini-3.1-flash-lite",
        "provider": "gemini",
        "supports_vision": True,
        "input_token_rate_per_million": "0.25",
        "output_token_rate_per_million": "1.50",
        "tier": "flash",
    },
    {
        "name": "Gemini 3.5 Flash",
        "identifier": "gemini-3.5-flash",
        "provider": "gemini",
        "supports_vision": True,
        "input_token_rate_per_million": "1.50",
        "output_token_rate_per_million": "9.00",
        "tier": "flash",
    },
    {
        "name": "GPT-5.5",
        "identifier": "gpt-5.5",
        "provider": "openai",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": False,
        "input_token_rate_per_million": "5.00",
        "output_token_rate_per_million": "30.00",
        "tier": "premium",
    },
    {
        "name": "GPT-5.4",
        "identifier": "gpt-5.4",
        "provider": "openai",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": True,
        "input_token_rate_per_million": "2.50",
        "output_token_rate_per_million": "15.00",
        "tier": "premium",
    },
    {
        "name": "GPT-5.4 Mini",
        "identifier": "gpt-5.4-mini",
        "provider": "openai",
        "is_reasoning": True,
        "supports_vision": True,
        "supports_temperature": True,
        "input_token_rate_per_million": "0.75",
        "output_token_rate_per_million": "4.50",
        "tier": "flash",
    },
    {
        "name": "Claude Opus 4.7",
        "identifier": "claude-opus-4-7",
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
    {
        "name": "Claude Opus 4.8",
        "identifier": "claude-opus-4-8",
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


def seed_modern_llm_models(apps, schema_editor):
    """
    Seed current chat model rows without changing existing environment-specific
    configuration. Existing identifiers are intentionally left untouched.
    """
    LLM = apps.get_model("conversations", "LLM")

    created_count = 0
    skipped_count = 0

    for llm in MODERN_LLM_DATA:
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

    for llm in MODERN_LLM_DATA:
        LLM.objects.filter(identifier=llm["identifier"]).update(
            input_token_rate_per_million=llm["input_token_rate_per_million"],
            output_token_rate_per_million=llm["output_token_rate_per_million"],
        )

    LLM.objects.filter(
        provider="openai",
        identifier__in=OPENAI_MAX_COMPLETION_TOKEN_MODELS,
    ).update(
        is_reasoning=True,
    )

    LLM.objects.filter(
        provider="openai",
        identifier__in=OPENAI_MAX_COMPLETION_TOKEN_MODELS,
    ).exclude(identifier__in=OPENAI_UNSUPPORTED_TEMPERATURE_MODELS).update(
        supports_temperature=True,
    )

    LLM.objects.filter(
        provider="openai",
        identifier__in=OPENAI_UNSUPPORTED_TEMPERATURE_MODELS,
    ).update(
        supports_temperature=False,
    )

    LLM.objects.filter(
        provider="gemini",
        identifier__in=[*DEPRECATED_GEMINI_IDENTIFIERS, *NON_PICKER_GEMINI_IDENTIFIERS],
    ).update(is_active=False)

    print(
        "\nModern LLM Seed Migration: "
        f"Created {created_count}, Skipped {skipped_count} (already exist)\n"
    )


def reverse_seed_modern_llm_models(apps, schema_editor):
    """
    Remove only unreferenced rows from this seed list.
    """
    LLM = apps.get_model("conversations", "LLM")

    identifiers_to_remove = [
        *[llm["identifier"] for llm in MODERN_LLM_DATA],
        *REVERSE_ONLY_IDENTIFIERS,
    ]

    for identifier in identifiers_to_remove:
        try:
            llm = LLM.objects.get(identifier=identifier)
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
        ("conversations", "0074_model_capabilities_and_conversation_effort"),
    ]

    operations = [
        migrations.RunPython(seed_modern_llm_models, reverse_seed_modern_llm_models),
    ]
