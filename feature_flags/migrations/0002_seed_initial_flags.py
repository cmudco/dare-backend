from django.db import migrations

from feature_flags.constants import DEFAULT_FLAG_DEFINITIONS


def seed_flags(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    for definition in DEFAULT_FLAG_DEFINITIONS:
        FeatureFlag.objects.update_or_create(
            key=definition["key"],
            defaults={
                "description": definition["description"],
                "default_enabled": definition["default_enabled"],
            },
        )


def unseed_flags(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(
        key__in=[d["key"] for d in DEFAULT_FLAG_DEFINITIONS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_flags, reverse_code=unseed_flags),
    ]
