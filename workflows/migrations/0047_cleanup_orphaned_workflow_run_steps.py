# Generated migration to clean up orphaned WorkflowRunStep records
#
# BACKGROUND:
# During the migration from legacy Step model to node-based WorkflowNode architecture,
# some WorkflowRunStep records were created with step_node=NULL. These orphaned records
# cause AttributeError when trying to access step_node.node_id in the node_execution_state_builder.
#
# WHAT THIS MIGRATION DOES:
# Safely removes WorkflowRunStep records where step_node is NULL.
#
# WHAT DATA WILL BE LOST:
# - Only orphaned WorkflowRunStep records (those without a valid step_node reference)
# - These records are from the migration period and cannot be properly displayed/used anyway
# - The parent WorkflowRun records will remain intact
# - No cascade deletions will occur (no other models reference WorkflowRunStep)
#
# SAFETY CHECKS:
# 1. Only deletes records where step_node IS NULL
# 2. Logs count of affected records before deletion
# 3. No cascade impact (verified: no ForeignKeys point to WorkflowRunStep)
# 4. Parent WorkflowRun records are preserved
#
# WHEN TO RUN:
# - Safe to run in production/staging
# - Run BEFORE deploying code that expects step_node to be non-null
# - Recommended: Take database backup first as a precaution

from django.db import migrations


def cleanup_orphaned_steps(apps, schema_editor):
    """
    Remove WorkflowRunStep records with null step_node.
    These are orphaned records from the migration period.
    """
    WorkflowRunStep = apps.get_model('workflows', 'WorkflowRunStep')

    # Count orphaned records
    orphaned_count = WorkflowRunStep.objects.filter(step_node__isnull=True).count()

    if orphaned_count > 0:
        print(f"\n⚠️  Found {orphaned_count} orphaned WorkflowRunStep records (step_node is NULL)")
        print("   These records are from the legacy migration period and cannot be used.")
        print("   Deleting orphaned records...")

        # Delete orphaned records
        deleted_count, _ = WorkflowRunStep.objects.filter(step_node__isnull=True).delete()

        print(f"✅ Successfully deleted {deleted_count} orphaned WorkflowRunStep records")
    else:
        print("\n✅ No orphaned WorkflowRunStep records found. Database is clean.")


def reverse_cleanup(apps, schema_editor):
    """
    Reverse migration - cannot restore deleted data.
    This is a data cleanup migration, so reverse is a no-op.
    """
    print("\n⚠️  WARNING: Cannot restore deleted orphaned records.")
    print("   If you need to recover data, restore from database backup.")


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0046_alter_startnodedata_description_and_more'),
    ]

    operations = [
        migrations.RunPython(
            cleanup_orphaned_steps,
            reverse_cleanup,
        ),
    ]
