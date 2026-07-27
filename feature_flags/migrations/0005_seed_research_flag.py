"""Seed the Research Mode release flag disabled by default."""

from django.db import migrations


KEY = "enable_research"
DESCRIPTION = "Research Mode projects, agents, and research APIs."


def seed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.update_or_create(
        key=KEY,
        defaults={"description": DESCRIPTION, "default_enabled": False},
    )


def unseed(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(key=KEY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0004_seed_litellm_wallet_flag"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
