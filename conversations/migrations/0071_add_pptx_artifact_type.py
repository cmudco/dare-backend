from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0070_message_litellm_audit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="artifact",
            name="artifact_type",
            field=models.CharField(
                choices=[
                    ("document", "Document"),
                    ("code", "Code"),
                    ("diagram", "Diagram"),
                    ("chart", "Chart"),
                    ("react", "React Component"),
                    ("docx", "Word Document"),
                    ("pptx", "PowerPoint Presentation"),
                ],
                default="document",
                help_text="Type of artifact (document, code, diagram).",
                max_length=20,
            ),
        ),
    ]
