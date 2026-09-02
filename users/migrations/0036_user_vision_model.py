from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0035_bump_legacy_chunk_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="vision_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Preferred model for scanned-page transcription and figure description; empty means the wallet's recommendation.",
                max_length=255,
                verbose_name="Vision Model",
            ),
        ),
    ]
