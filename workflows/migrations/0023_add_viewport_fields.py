# Generated migration to fix viewport field issues
# This adds the viewport_x, viewport_y, viewport_zoom fields that were missing from 0018
# Migration 0020 removed viewport JSONField before these fields were added, causing errors

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0022_make_step_id_nullable'),
    ]

    operations = [
        # Add the viewport fields that should have been added in 0018
        # but were only documented in comments
        migrations.AddField(
            model_name='workflow',
            name='viewport_x',
            field=models.FloatField(default=0.0, help_text='Viewport X position'),
        ),
        migrations.AddField(
            model_name='workflow',
            name='viewport_y',
            field=models.FloatField(default=0.0, help_text='Viewport Y position'),
        ),
        migrations.AddField(
            model_name='workflow',
            name='viewport_zoom',
            field=models.FloatField(default=1.0, help_text='Viewport zoom level'),
        ),
    ]
