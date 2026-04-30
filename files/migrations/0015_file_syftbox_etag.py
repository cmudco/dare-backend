from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0014_file_source_file_fileshare"),
    ]

    operations = [
        migrations.AddField(
            model_name="file",
            name="syftbox_etag",
            field=models.CharField(
                blank=True,
                help_text="Last known SyftBox ETag used to detect remote content changes",
                max_length=128,
                null=True,
                verbose_name="SyftBox ETag",
            ),
        ),
    ]
