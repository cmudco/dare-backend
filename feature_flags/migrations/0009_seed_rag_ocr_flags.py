"""Seed the document sources and scanned-page transcription switches, on by default."""

from django.db import migrations

FLAGS = {
    "enable_rag": (
        "Document sources: uploads, search over documents in chat, projects, "
        "agents and workflows, and knowledge graphs. When disabled, the UI "
        "hides them and the backend refuses uploads and ignores sources "
        "attached to a message."
    ),
    "enable_ocr": (
        "Vision transcription of scanned pages and figure descriptions during "
        "document processing. When disabled, documents are indexed from their "
        "readable text only."
    ),
}


def seed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    for key, description in FLAGS.items():
        FeatureFlag.objects.update_or_create(
            key=key,
            defaults={"description": description, "default_enabled": True},
        )


def unseed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(key__in=FLAGS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0008_seed_research_tools_flag"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
