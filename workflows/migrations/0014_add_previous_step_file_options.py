# Generated manually for workflow file inheritance feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0013_workflowstepsnippet'),
    ]

    operations = [
        migrations.AddField(
            model_name='step',
            name='use_previous_step_files',
            field=models.BooleanField(default=False, help_text='If True, inherit files from the previous step in the workflow instead of using manually selected files.'),
        ),
        migrations.AddField(
            model_name='step',
            name='use_previous_step_embeddings',
            field=models.BooleanField(default=False, help_text='If True, inherit embeddings from the previous step in the workflow instead of using manually selected embeddings.'),
        ),
    ]