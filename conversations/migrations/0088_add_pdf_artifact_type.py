from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0087_fix_claude_model_catalog"),
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
                    ("pdf", "PDF Document"),
                ],
                default="document",
                help_text="Type of artifact (document, code, diagram).",
                max_length=20,
            ),
        ),
    ]
