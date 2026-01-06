# Generated migration for structured output node independence refactoring

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0041_workflow_manual_mode_enabled'),
    ]

    operations = [
        # Add text_input field to StructuredOutputNodeData
        migrations.AddField(
            model_name='structuredoutputnodedata',
            name='text_input',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Optional text input to be passed directly to the LLM for routing decision'
            ),
        ),

        # Remove use_structured_output_node from StepNodeData since structured output is now independent
        migrations.RemoveField(
            model_name='stepnodedata',
            name='use_structured_output_node',
        ),
    ]
