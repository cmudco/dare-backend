from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0015_merge_20250803_0348'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflow',
            name='layout',
            field=models.JSONField(default=dict, blank=True, help_text="Optional React Flow layout data: positions keyed by 'start', 'step:<order>', 'output:<order>'"),
        ),
    ]


