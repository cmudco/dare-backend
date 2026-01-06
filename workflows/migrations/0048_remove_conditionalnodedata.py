# Generated manually - removes ConditionalNodeData model
# The structured output node provides the same routing functionality

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0047_cleanup_orphaned_workflow_run_steps'),
    ]

    operations = [
        # Step 1: Delete WorkflowRunStep records that reference conditional nodes
        # This prevents foreign key constraint violations
        migrations.RunSQL(
            sql="""
                DELETE FROM workflows_workflowrunstep 
                WHERE step_node_id IN (
                    SELECT id FROM workflows_workflownode 
                    WHERE node_type = 'conditional'
                );
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 2: Delete WorkflowEdge records connected to conditional nodes
        migrations.RunSQL(
            sql="""
                DELETE FROM workflows_workflowedge 
                WHERE source IN (
                    SELECT node_id FROM workflows_workflownode 
                    WHERE node_type = 'conditional'
                ) OR target IN (
                    SELECT node_id FROM workflows_workflownode 
                    WHERE node_type = 'conditional'
                );
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 3: Delete the WorkflowNode records for conditional nodes
        migrations.RunSQL(
            sql="""
                DELETE FROM workflows_workflownode 
                WHERE node_type = 'conditional';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 4: Delete all ConditionalNodeData records
        migrations.RunSQL(
            sql="""
                DELETE FROM workflows_conditionalnodedata;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Step 5: Now delete the ConditionalNodeData table
        migrations.DeleteModel(
            name='ConditionalNodeData',
        ),
    ]

