from django.db import migrations, models

from files.services.source_size import count_tokens

BATCH_SIZE = 200


def backfill(apps, schema_editor):
    File = apps.get_model("files", "File")
    files = (
        File._base_manager.filter(extracted_text__isnull=False)
        .only("id", "extracted_text")
        .iterator(chunk_size=BATCH_SIZE)
    )
    batch = []
    for file in files:
        file.token_count = count_tokens(file.extracted_text)
        batch.append(file)
        if len(batch) == BATCH_SIZE:
            File._base_manager.bulk_update(batch, ["token_count"])
            batch = []
    File._base_manager.bulk_update(batch, ["token_count"])


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0029_sandboxupload_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="file",
            name="token_count",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Tokens in the extracted text; empty until the file is parsed.",
                null=True,
                verbose_name="Token Count",
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
