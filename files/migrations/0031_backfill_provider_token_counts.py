"""Count existing documents once per provider, as uploads are counted now."""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
PROCESSED = 1


def backfill(apps, schema_editor):
    # Live service code: the counts come from provider APIs, not the schema.
    from core.services.token_counting import store_provider_counts
    from files.models import File as LiveFile

    File = apps.get_model("files", "File")
    ids = list(
        File._base_manager.filter(
            is_active=True, is_deleted=False, is_media=False, status=PROCESSED
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    for start in range(0, len(ids), BATCH_SIZE):
        batch = ids[start : start + BATCH_SIZE]
        store_provider_counts(LiveFile._base_manager.filter(pk__in=batch))
        logger.info("Token counts: %s of %s files", start + len(batch), len(ids))


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0030_file_token_counts"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop, elidable=True),
    ]
