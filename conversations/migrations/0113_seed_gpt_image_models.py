from django.db import migrations

# OpenAI shut down dall-e-2 and dall-e-3 on 2026-05-12 and replaced them with
# the GPT Image family, billed per token:
# https://developers.openai.com/api/docs/deprecations
# https://developers.openai.com/api/docs/pricing
GPT_IMAGE_MODELS = [
    {
        "name": "GPT Image 2.6 Sunburst",
        "identifier": "gpt-image-2.6-sunburst",
        "description": "OpenAI image generation model for detailed visual work.",
    },
    {
        "name": "GPT Image 2.6 Flare",
        "identifier": "gpt-image-2.6-flare",
        "description": "OpenAI image generation model for fast iteration.",
    },
]

SHARED_FIELDS = {
    "provider": "openai",
    "tier": "advanced",
    "is_image_generator": True,
    "supports_temperature": False,
    "input_token_rate_per_million": "5.00",
    "output_token_rate_per_million": "30.00",
}

RETIRED_IDENTIFIERS = ["dall-e-2", "dall-e-3"]


def seed_gpt_image_models(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")

    for model in GPT_IMAGE_MODELS:
        LLM.objects.get_or_create(
            identifier=model["identifier"],
            defaults={
                "name": model["name"],
                "description": model["description"],
                **SHARED_FIELDS,
            },
        )

    LLM.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(is_active=False)


def reverse_seed_gpt_image_models(apps, schema_editor):
    """Reactivate DALL-E and drop the seeded rows nothing references."""
    LLM = apps.get_model("conversations", "LLM")

    LLM.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(is_active=True)

    for llm in LLM.objects.filter(
        identifier__in=[model["identifier"] for model in GPT_IMAGE_MODELS]
    ):
        referenced = False
        for model in apps.get_models():
            for field in model._meta.local_fields:
                if getattr(field.remote_field, "model", None) is not LLM:
                    continue
                if model._base_manager.filter(**{field.attname: llm.pk}).exists():
                    referenced = True
            for field in model._meta.local_many_to_many:
                if getattr(field.remote_field, "model", None) is not LLM:
                    continue
                if model._base_manager.filter(**{field.name: llm.pk}).exists():
                    referenced = True
        if not referenced:
            llm.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0112_alter_conversation_rag_mode"),
    ]

    operations = [
        migrations.RunPython(seed_gpt_image_models, reverse_seed_gpt_image_models),
    ]
