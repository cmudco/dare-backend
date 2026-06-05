from django.db import migrations

KEY = "enable_research"
DESCRIPTION = "Show and allow access to the Research workspace."


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
