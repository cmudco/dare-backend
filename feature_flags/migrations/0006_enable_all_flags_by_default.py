"""Enable every feature flag by default.

Resolution precedence is user override > group override > flag ``default_enabled``
(see ``feature_flags.services``). New accounts carry no overrides, so flipping
each flag's ``default_enabled`` to ``True`` means a freshly registered user sees
every feature turned on out of the box.

This is a *soft* migration: it only moves the app-wide default. Existing group
and user overrides still win, so anyone who has explicitly disabled a feature
keeps that choice.

Reverse restores each flag's default from ``DEFAULT_FLAG_DEFINITIONS`` (the
source of truth for the conservative production tier). Any flag not present in
that list is left enabled on reverse, since its pre-migration default is unknown.
"""

from django.db import migrations

from feature_flags.constants import DEFAULT_FLAG_DEFINITIONS


def enable_all(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.update(default_enabled=True)


def restore_defaults(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    for definition in DEFAULT_FLAG_DEFINITIONS:
        FeatureFlag.objects.filter(key=definition["key"]).update(
            default_enabled=definition["default_enabled"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0005_seed_research_flag"),
    ]

    operations = [
        migrations.RunPython(enable_all, reverse_code=restore_defaults),
    ]
