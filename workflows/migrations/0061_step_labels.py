from django.db import migrations, models


def backfill_labels(apps, schema_editor):
    model_names = [
        "StepNodeData",
        "ChatOutputNodeData",
        "StructuredOutputNodeData",
        "FileNodeData",
    ]
    for model_name in model_names:
        model = apps.get_model("workflows", model_name)
        for instance in model.objects.all().iterator():
            model.objects.filter(id=instance.id).update(label=str(instance.step_number))


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0060_workflowrun_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="stepnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="User-editable label for this step.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="chatoutputnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="User-editable label for the connected output.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="structuredoutputnodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="User-editable label for this routing node.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="filenodedata",
            name="label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="User-editable label for this file node.",
                max_length=255,
            ),
        ),
        migrations.RunPython(backfill_labels, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="stepnodedata",
            name="step_number",
        ),
        migrations.RemoveField(
            model_name="chatoutputnodedata",
            name="step_number",
        ),
        migrations.RemoveField(
            model_name="structuredoutputnodedata",
            name="step_number",
        ),
        migrations.RemoveField(
            model_name="filenodedata",
            name="step_number",
        ),
    ]
