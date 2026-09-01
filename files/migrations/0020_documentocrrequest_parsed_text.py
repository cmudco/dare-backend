from django.db import migrations, models


def backfill_fully_scanned_documents(apps, schema_editor):
    DocumentOcrRequest = apps.get_model("files", "DocumentOcrRequest")
    DocumentOcrRequest.objects.filter(
        file__page_count__gt=0,
        file__pages_without_text=models.F("file__page_count"),
        parsed_text__isnull=True,
    ).update(parsed_text="")


class Migration(migrations.Migration):
    dependencies = [("files", "0019_document_ocr_request")]

    operations = [
        migrations.AddField(
            model_name="documentocrrequest",
            name="parsed_text",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_fully_scanned_documents,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
