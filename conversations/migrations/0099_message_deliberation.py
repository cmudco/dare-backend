import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0098_update_gpt_5_6_sol_rates"),
        ("workflows", "0074_workflow_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="deliberation",
            field=models.JSONField(
                blank=True,
                help_text="Panel/council behind this answer: every responder's draft, peer reviews, and the chairman, for the deliberation UI.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="workflow_run",
            field=models.ForeignKey(
                blank=True,
                help_text="The ensemble workflow run that produced this answer, when a panel or council answered.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="chat_messages",
                to="workflows.workflowrun",
            ),
        ),
    ]
