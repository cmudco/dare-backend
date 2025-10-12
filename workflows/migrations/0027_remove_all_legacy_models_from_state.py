# Final cleanup: Remove all legacy models and fields from Django's migration state
# These were removed in previous migrations but Django still tracks them

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0026_sync_workflow_migration_state'),
    ]

    operations = [
        # State-only operations - no SQL executed, just updating Django's state tracking
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            
            state_operations=[
                # Remove legacy Step model references
                migrations.RemoveField(
                    model_name='step',
                    name='embeddings',
                ),
                migrations.RemoveField(
                    model_name='step',
                    name='files',
                ),
                migrations.RemoveField(
                    model_name='step',
                    name='llm',
                ),
                migrations.RemoveField(
                    model_name='step',
                    name='prompt',
                ),
                migrations.RemoveField(
                    model_name='step',
                    name='user',
                ),
                
                # Remove legacy WorkflowRunStep fields
                migrations.RemoveField(
                    model_name='workflowrunstep',
                    name='step_id',
                ),
                migrations.RemoveField(
                    model_name='workflowrunstep',
                    name='step',
                ),
                
                # Remove legacy WorkflowStepSnippet references
                migrations.RemoveField(
                    model_name='workflowstepsnippet',
                    name='file',
                ),
                migrations.RemoveField(
                    model_name='workflowstepsnippet',
                    name='workflow_run_step',
                ),
                
                # Remove constraints
                migrations.RemoveConstraint(
                    model_name='workflowedge',
                    name='unique_workflow_edge',
                ),
                migrations.RemoveConstraint(
                    model_name='workflownode',
                    name='unique_workflow_node',
                ),
                
                # Delete the legacy models entirely from state
                migrations.DeleteModel(
                    name='Step',
                ),
                migrations.DeleteModel(
                    name='WorkflowStepSnippet',
                ),
            ],
        ),
    ]


