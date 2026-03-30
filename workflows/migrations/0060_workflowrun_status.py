from django.db import migrations, models


def backfill_workflow_run_status(apps, schema_editor):
    WorkflowRun = apps.get_model("workflows", "WorkflowRun")
    WorkflowRunStep = apps.get_model("workflows", "WorkflowRunStep")

    completed_statuses = {"completed", "skipped"}

    for run in WorkflowRun.objects.all().iterator():
        step_statuses = list(
            WorkflowRunStep.objects.filter(workflow_run_id=run.id).values_list("status", flat=True)
        )

        if not step_statuses:
            status = "running"
        elif "pending_human_input" in step_statuses:
            status = "pending_human_input"
        elif "failed" in step_statuses:
            status = "failed"
        elif "running" in step_statuses:
            status = "running"
        elif all(step_status in completed_statuses for step_status in step_statuses):
            status = "completed"
        elif run.is_partial and any(step_status in completed_statuses for step_status in step_statuses):
            status = "completed"
        else:
            status = "running"

        WorkflowRun.objects.filter(id=run.id).update(status=status)


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0059_alter_batchrun_managers"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                    ("pending_human_input", "Pending Human Input"),
                ],
                default="running",
                help_text="Current status of the workflow run.",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_workflow_run_status, migrations.RunPython.noop),
    ]
