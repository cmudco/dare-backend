"""
Seed the ``enable_litellm_wallet`` flag with ``default_enabled=True``.
LiteLLM wallets were unconditional before this migration — keeping the default
on preserves existing behavior on rollout. Admins can disable per-group or
per-user from Django admin.
"""

from django.db import migrations


KEY = "enable_litellm_wallet"
DESCRIPTION = (
    "Allow users to add and select LiteLLM proxy keys as the active wallet. "
    "When disabled, LiteLLM wallets are hidden in the UI and the wallet "
    "router falls back to DARE."
)


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
        ("feature_flags", "0003_drop_socket_io_flag"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=unseed),
    ]
