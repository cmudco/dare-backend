"""
Move the legacy ``BYOKeyFeatureFlag`` singleton's value into the unified
``feature_flags.FeatureFlag`` row keyed ``enable_byok``. Run BEFORE the
schema migration that drops the legacy model so production deployments that
had BYO enabled don't silently flip back off.
"""

from django.db import migrations


SINGLETON_PK = 1
UNIFIED_FLAG_KEY = "enable_byok"


def migrate_byo_flag(apps, schema_editor):
    BYOKeyFeatureFlag = apps.get_model("billing", "BYOKeyFeatureFlag")
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")

    legacy = BYOKeyFeatureFlag.objects.filter(pk=SINGLETON_PK).first()
    if legacy is None:
        # Singleton was never written — leave the unified flag at its seeded
        # default (False) and move on.
        return

    FeatureFlag.objects.update_or_create(
        key=UNIFIED_FLAG_KEY,
        defaults={"default_enabled": bool(legacy.is_enabled)},
    )


def restore_byo_flag(apps, schema_editor):
    # Reverse copy: write the unified flag's default back to the singleton so
    # rolling back leaves both stores in sync.
    BYOKeyFeatureFlag = apps.get_model("billing", "BYOKeyFeatureFlag")
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")

    flag = FeatureFlag.objects.filter(key=UNIFIED_FLAG_KEY).first()
    enabled = bool(flag.default_enabled) if flag else False

    legacy, _created = BYOKeyFeatureFlag.objects.get_or_create(pk=SINGLETON_PK)
    legacy.is_enabled = enabled
    legacy.save(update_fields=["is_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0018_merge_20260507_1600"),
        ("feature_flags", "0002_seed_initial_flags"),
    ]

    operations = [
        migrations.RunPython(migrate_byo_flag, reverse_code=restore_byo_flag),
    ]
