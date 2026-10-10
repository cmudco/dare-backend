from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def folder_and_tag_plans_to_actions(apps, schema_editor):
    """Rewrite {folders, tags} plans as add_to_folder / add_tag actions.

    Proposals applied before this release have no undo journal, so their
    actions are applied with a null journal: undoing one reports that there
    is nothing to revert.
    """
    Proposal = apps.get_model("assistant", "AssistantProposal")
    for proposal in Proposal.objects.all():
        applied = proposal.status == "applied"
        groups = [
            ("add_to_folder", "name", g) for g in proposal.plan.get("folders", [])
        ]
        groups += [("add_tag", "label", g) for g in proposal.plan.get("tags", [])]
        proposal.plan = {
            "actions": [
                {
                    "id": str(index),
                    "type": kind,
                    "name": group[key],
                    "is_new": group.get("is_new", False),
                    "description": "",
                    "files": group["files"],
                    "conversations": [],
                    "status": "applied" if applied else "pending",
                    "notes": [],
                    "journal": None,
                }
                for index, (kind, key, group) in enumerate(groups, start=1)
            ]
        }
        proposal.save(update_fields=["plan"])


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel("FileOrganizationProposal", "AssistantProposal"),
        migrations.AlterModelOptions(
            name="assistantproposal",
            options={
                "ordering": ["created_at"],
                "verbose_name": "assistant proposal",
                "verbose_name_plural": "assistant proposals",
            },
        ),
        migrations.RunPython(
            folder_and_tag_plans_to_actions, migrations.RunPython.noop
        ),
        migrations.RemoveField(model_name="assistantproposal", name="outcome"),
        migrations.AlterField(
            model_name="assistantproposal",
            name="user",
            field=models.ForeignKey(
                help_text="User whose files, chats and projects the plan changes.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assistant_proposals",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="assistantproposal",
            name="plan",
            field=models.JSONField(
                help_text="{actions: [{id, type, name, files, conversations, status, ...}]}."
            ),
        ),
        migrations.AlterField(
            model_name="assistantproposal",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Waiting for the user"),
                    ("partially_applied", "Some changes applied"),
                    ("applied", "Applied"),
                    ("discarded", "Discarded"),
                ],
                default="pending",
                help_text="How much of the plan is applied, or that it was discarded.",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="assistantproposal",
            name="decided_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the user last applied, undid or discarded part of it.",
                null=True,
            ),
        ),
    ]
