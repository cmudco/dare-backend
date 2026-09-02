from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0073_drop_legacy_step_tables"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="kind",
            field=models.CharField(
                choices=[("user", "User"), ("ensemble", "Ensemble")],
                db_index=True,
                default="user",
                help_text="'user' workflows appear in the builder; 'ensemble' workflows are compiled from the chat model picker and run behind panel/council turns.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="workflow",
            name="ensemble_signature",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Depth + model line-up an ensemble workflow was compiled from, for reuse across turns.",
                max_length=255,
                null=True,
            ),
        ),
    ]
