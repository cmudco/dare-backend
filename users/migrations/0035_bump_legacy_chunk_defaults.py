from django.db import migrations

from core.config.processing import CHUNK_SIZE, OVERLAP_SIZE

# The old per-user defaults. Rows still holding both values never customized
# chunking, so they should follow the new CMU-matched defaults (see
# core/config/processing.py). Users with any other combination chose their own
# values and are left alone.
LEGACY_CHUNK_SIZE = 500
LEGACY_OVERLAP_SIZE = 100


def bump_legacy_defaults(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(
        chunk_size=LEGACY_CHUNK_SIZE, overlap_size=LEGACY_OVERLAP_SIZE
    ).update(chunk_size=CHUNK_SIZE, overlap_size=OVERLAP_SIZE)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0034_alter_user_chunk_size_alter_user_overlap_size"),
    ]

    operations = [
        migrations.RunPython(bump_legacy_defaults, migrations.RunPython.noop),
    ]
