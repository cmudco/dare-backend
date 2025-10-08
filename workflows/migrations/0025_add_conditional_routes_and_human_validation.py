# Generated manually - adds n-routes and human validation support to ConditionalNodeData

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0024_cleanup_legacy_workflow_fields"),
    ]

    operations = [
        # Add new fields for n-routes support
        migrations.AddField(
            model_name="conditionalnodedata",
            name="routes",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of route definitions: [{'name': 'Route A', 'description': '...'}, ...]",
            ),
        ),
        # Add human validation toggle
        migrations.AddField(
            model_name="conditionalnodedata",
            name="require_human_validation",
            field=models.BooleanField(
                default=False,
                help_text="If true, pause execution and ask user to choose route",
            ),
        ),
        # Make legacy route fields nullable for backward compatibility
        migrations.AlterField(
            model_name="conditionalnodedata",
            name="route_a_name",
            field=models.CharField(
                blank=True,
                max_length=100,
                null=True,
                help_text="DEPRECATED: Use routes field instead",
            ),
        ),
        migrations.AlterField(
            model_name="conditionalnodedata",
            name="route_b_name",
            field=models.CharField(
                blank=True,
                max_length=100,
                null=True,
                help_text="DEPRECATED: Use routes field instead",
            ),
        ),
        migrations.AlterField(
            model_name="conditionalnodedata",
            name="route_a_description",
            field=models.TextField(
                blank=True,
                help_text="DEPRECATED: Use routes field instead",
            ),
        ),
        migrations.AlterField(
            model_name="conditionalnodedata",
            name="route_b_description",
            field=models.TextField(
                blank=True,
                help_text="DEPRECATED: Use routes field instead",
            ),
        ),
    ]


