from django.db import migrations, models


def rename_parser(apps, schema_editor, old, new):
    File = apps.get_model("files", "File")
    files = File._base_manager.using(schema_editor.connection.alias)
    files.filter(parser_name=old).update(parser_name=new)
    for file in files.filter(document_model__parser=old).iterator(chunk_size=500):
        document = dict(file.document_model)
        document["parser"] = new
        files.filter(pk=file.pk).update(document_model=document)


def forwards(apps, schema_editor):
    rename_parser(apps, schema_editor, "legacy", "basic")


def backwards(apps, schema_editor):
    rename_parser(apps, schema_editor, "basic", "legacy")


class Migration(migrations.Migration):
    dependencies = [("files", "0024_file_processing_mode")]
    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="file",
            name="parser_name",
            field=models.CharField(
                verbose_name="Parser",
                blank=True,
                null=True,
                max_length=32,
                help_text="Parser that produced the extracted text (basic, docling, or notebook)",
            ),
        ),
    ]
