# Generated manually for workflow file inheritance feature

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0013_workflowstepsnippet'),
    ]

    # This migration duplicates the fields added by
    # 0014_step_use_previous_step_embeddings_and_more. Keep the migration node
    # so databases retain a consistent migration history, but do not run the
    # duplicate schema operations.
    operations = []
