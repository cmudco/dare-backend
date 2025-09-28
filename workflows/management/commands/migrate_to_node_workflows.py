"""
Django management command to migrate old workflow structure to new node-based structure.

This command safely migrates data from the old workflow structure:
- workflows_workflow (with title, description, mode fields)
- workflows_step (with order, prompt, etc.)
- workflows_workflow_steps (many-to-many relationship)

To the new node-based structure:
- workflows_workflow (container only)
- workflows_workflownode (React Flow nodes)
- workflows_startnodedata (workflow metadata)
- workflows_stepnodedata (step data)

Usage:
    python manage.py migrate_to_node_workflows --dry-run    # Test without making changes
    python manage.py migrate_to_node_workflows --confirm     # Actually perform migration
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.contrib.contenttypes.models import ContentType
from workflows.models.core import Workflow
from workflows.models.graph import WorkflowNode
from workflows.models.nodes import StartNodeData, StepNodeData
import json


class Command(BaseCommand):
    help = 'Migrate old workflow structure to new node-based structure'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without making changes',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually perform the migration (required to make changes)',
        )
        parser.add_argument(
            '--workflow-id',
            type=int,
            help='Migrate only a specific workflow ID',
        )

    def handle(self, *args, **options):
        if not options['dry_run'] and not options['confirm']:
            raise CommandError(
                "You must specify either --dry-run to test or --confirm to actually migrate."
            )

        if options['dry_run']:
            self.stdout.write(
                self.style.WARNING('🔍 DRY RUN MODE - No changes will be made')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('🚀 MIGRATION MODE - Changes will be made')
            )

        try:
            with connection.cursor() as cursor:
                # Check if old structure exists
                old_workflows = self._get_old_workflows(cursor, options.get('workflow_id'))

                if not old_workflows:
                    self.stdout.write(
                        self.style.WARNING('No old workflows found to migrate.')
                    )
                    return

                self.stdout.write(f'Found {len(old_workflows)} workflow(s) to migrate:')

                for workflow in old_workflows:
                    self._display_workflow_info(workflow, cursor)

                if options['dry_run']:
                    self._perform_dry_run(old_workflows, cursor)
                else:
                    self._perform_migration(old_workflows, cursor)

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Migration failed: {str(e)}')
            )
            raise

    def _get_old_workflows(self, cursor, workflow_id=None):
        """Fetch old workflow data from database."""
        if workflow_id:
            cursor.execute("""
                SELECT id, title, description, mode, user_id, created_at, updated_at,
                       is_active, is_deleted, version, parent_id, viewport
                FROM workflows_workflow
                WHERE id = %s
            """, [workflow_id])
        else:
            cursor.execute("""
                SELECT id, title, description, mode, user_id, created_at, updated_at,
                       is_active, is_deleted, version, parent_id, viewport
                FROM workflows_workflow
                ORDER BY id
            """)

        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _get_workflow_steps(self, cursor, workflow_id):
        """Fetch steps for a specific workflow."""
        cursor.execute("""
            SELECT s.id, s."order", s.prompt_id, s.user_id, s.llm_id, s.max_tokens,
                   s.temperature, s.document_similarity_threshold, s.max_context_snippets,
                   s.use_previous_step_files, s.use_previous_step_embeddings,
                   s.created_at, s.updated_at
            FROM workflows_step s
            INNER JOIN workflows_workflow_steps ws ON s.id = ws.step_id
            WHERE ws.workflow_id = %s
            ORDER BY s."order"
        """, [workflow_id])

        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _display_workflow_info(self, workflow, cursor):
        """Display information about a workflow to be migrated."""
        steps = self._get_workflow_steps(cursor, workflow['id'])

        self.stdout.write(f"\n📋 Workflow ID {workflow['id']}:")
        self.stdout.write(f"  Title: {workflow['title']}")
        self.stdout.write(f"  Description: {workflow['description'][:100]}{'...' if len(workflow['description']) > 100 else ''}")
        self.stdout.write(f"  Mode: {workflow['mode']} ({'Sequential' if workflow['mode'] == 1 else 'Parallel'})")
        self.stdout.write(f"  User ID: {workflow['user_id']}")
        self.stdout.write(f"  Steps: {len(steps)}")

        for step in steps:
            self.stdout.write(f"    - Step {step['order']}: Prompt ID {step['prompt_id']}")

    def _perform_dry_run(self, old_workflows, cursor):
        """Show what would be created without making changes."""
        self.stdout.write("\n🔍 DRY RUN - Here's what would be created:\n")

        for workflow in old_workflows:
            steps = self._get_workflow_steps(cursor, workflow['id'])

            self.stdout.write(f"Workflow ID {workflow['id']} would create:")
            self.stdout.write("  1. New Workflow container (keeping same ID)")
            self.stdout.write("  2. StartNodeData with:")
            self.stdout.write(f"     - title: '{workflow['title']}'")
            self.stdout.write(f"     - description: '{workflow['description']}'")
            self.stdout.write(f"     - mode: '{'sequential' if workflow['mode'] == 1 else 'parallel'}'")
            self.stdout.write("  3. WorkflowNode (start type) linking to StartNodeData")

            for i, step in enumerate(steps):
                self.stdout.write(f"  {4 + i}. StepNodeData for step {step['order']}:")
                self.stdout.write(f"     - prompt_id: {step['prompt_id']}")
                self.stdout.write(f"     - step_number: {step['order']}")
                self.stdout.write(f"     - max_tokens: {step['max_tokens']}")
                self.stdout.write(f"     - temperature: {step['temperature']}")
                self.stdout.write(f"  {4 + len(steps) + i}. WorkflowNode (step type) linking to StepNodeData")

            self.stdout.write("")

    def _perform_migration(self, old_workflows, cursor):
        """Actually perform the migration with transaction safety."""
        self.stdout.write("\n🚀 Starting migration...\n")

        total_workflows = len(old_workflows)
        migrated_count = 0

        for workflow in old_workflows:
            try:
                with transaction.atomic():
                    self._migrate_single_workflow(workflow, cursor)
                    migrated_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✅ Successfully migrated workflow {workflow['id']} "
                            f"({migrated_count}/{total_workflows})"
                        )
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"❌ Failed to migrate workflow {workflow['id']}: {str(e)}"
                    )
                )
                # Continue with other workflows rather than stopping entirely
                continue

        self.stdout.write(
            self.style.SUCCESS(
                f"\n🎉 Migration completed! {migrated_count}/{total_workflows} workflows migrated successfully."
            )
        )

    def _migrate_single_workflow(self, old_workflow, cursor):
        """Migrate a single workflow to the new structure."""
        workflow_id = old_workflow['id']
        steps = self._get_workflow_steps(cursor, workflow_id)

        # Step 1: Check if this workflow already has nodes (avoid duplicate migration)
        if WorkflowNode.objects.filter(workflow_id=workflow_id).exists():
            raise Exception(f"Workflow {workflow_id} already has nodes - skipping to avoid duplicates")

        # Step 2: Get the workflow instance (should already exist from new models)
        try:
            workflow = Workflow.objects.get(id=workflow_id)
        except Workflow.DoesNotExist:
            raise Exception(f"Workflow {workflow_id} not found in new structure")

        # Step 3: Create StartNodeData
        start_data = StartNodeData.objects.create(
            title=old_workflow['title'] or 'Untitled Workflow',
            description=old_workflow['description'] or '',
            mode='sequential' if old_workflow['mode'] == 1 else 'parallel'
        )

        # Step 4: Create StartNode
        start_node = WorkflowNode.objects.create(
            workflow=workflow,
            node_id='start-node',
            node_type='start',
            position_x=100.0,
            position_y=100.0,
            data_content_type=ContentType.objects.get_for_model(StartNodeData),
            data_object_id=start_data.id
        )

        # Step 5: Create StepNodeData and WorkflowNodes for each step
        previous_node_id = 'start-node'

        for step in steps:
            # Create StepNodeData
            step_data = StepNodeData.objects.create(
                prompt_id=step['prompt_id'],
                step_number=step['order'],
                max_tokens=step['max_tokens'] or 2000,
                temperature=step['temperature'] or 0.7,
                document_similarity_threshold=step['document_similarity_threshold'] or 0.7,
                max_context_snippets=step['max_context_snippets'] or 5,
                use_previous_step_files=step['use_previous_step_files'] or False,
                use_previous_step_embeddings=step['use_previous_step_embeddings'] or False,
                llm_id=step['llm_id']
            )

            # Create WorkflowNode for step
            step_node_id = f'step-{step["order"]}'
            step_node = WorkflowNode.objects.create(
                workflow=workflow,
                node_id=step_node_id,
                node_type='step',
                position_x=100.0 + (step['order'] * 200),
                position_y=300.0,
                data_content_type=ContentType.objects.get_for_model(StepNodeData),
                data_object_id=step_data.id
            )

            # Note: We're not creating edges yet - that would require understanding
            # the workflow logic better. For now, just create the nodes.

        self.stdout.write(f"  Created {len(steps)} step nodes for workflow {workflow_id}")