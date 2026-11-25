from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0029_sandboxupload_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="file",
            name="token_counts",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Tokens in the extracted text per tokenizer, counted on first use: 'o200k' for OpenAI, '<provider>:<model>' for others. Cleared when the text changes.",
                verbose_name="Token Counts",
            ),
        ),
    ]
