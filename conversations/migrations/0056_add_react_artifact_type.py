# Generated manually for adding React component artifact type

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0055_add_unified_artifact_fields"),
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
                ],
                default="document",
                help_text="Type of artifact (document, code, diagram, chart, react).",
                max_length=20,
            ),
        ),
    ]
