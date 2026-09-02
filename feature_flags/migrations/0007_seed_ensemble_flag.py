"""Seed the panel/council chat flag disabled by default."""

from django.db import migrations

KEY = "enable_ensemble"
DESCRIPTION = (
    "Panel/council chat turns: the model picker's depth dial, the "
    "multi-model bench, and the deliberation view."
)


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
        ("feature_flags", "0006_enable_all_flags_by_default"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
