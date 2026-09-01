from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("memory", "0010_memorybackfillrun"),
    ]

    operations = [
        migrations.AddField(
            model_name="memorybackfillrun",
            name="since",
            field=models.DateField(
                blank=True,
                help_text="Optional inclusive start date for historical chat turns.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="memorybackfillrun",
            name="until",
            field=models.DateField(
                blank=True,
                help_text="Optional inclusive end date for historical chat turns.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="memorybackfillrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("stopped", "Stopped"),
                    ("failed", "Failed"),
                ],
                default="queued",
                help_text="The lifecycle state of this historical memory build.",
                max_length=16,
            ),
        ),
    ]
