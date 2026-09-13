from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0026_vectorindexattempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="vectorindexattempt",
            name="finished_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the generation stopped being current: retired, abandoned, or failed.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="vectorindexattempt",
            name="status",
            field=models.CharField(
                default="running",
                help_text="running, empty, published, or failed while the attempt is the newest; retired once a later generation replaced it; abandoned when the worker stopped before finishing.",
                max_length=16,
            ),
        ),
    ]
