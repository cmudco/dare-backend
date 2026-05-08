"""
Drop the ``enable_socket_io`` feature flag. Socket.IO is now the default and
only path; keeping the flag around invites confusion.
"""

from django.db import migrations


SOCKET_IO_KEY = "enable_socket_io"


def drop_socket_io_flag(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(key=SOCKET_IO_KEY).delete()


def restore_socket_io_flag(apps, schema_editor):
    # Reverse migration recreates the flag with its prior default so a rollback
    # leaves the system in the same shape as before.
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.update_or_create(
        key=SOCKET_IO_KEY,
        defaults={
            "description": (
                "Use the persistent Socket.IO connection for streaming."
            ),
            "default_enabled": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("feature_flags", "0002_seed_initial_flags"),
    ]

    operations = [
        migrations.RunPython(drop_socket_io_flag, reverse_code=restore_socket_io_flag),
    ]
