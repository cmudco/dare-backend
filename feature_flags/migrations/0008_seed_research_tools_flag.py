"""Seed the Research Tools sidebar link flag enabled by default."""

from django.db import migrations

KEY = "enable_research_tools"
DESCRIPTION = "Show the Research Tools link in the sidebar."


def seed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.update_or_create(
        key=KEY,
        defaults={"description": DESCRIPTION, "default_enabled": True},
    )


def unseed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(key=KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0007_seed_ensemble_flag"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
