"""
Workflow Run Repository

Single entry point for all WorkflowRun database operations.
Replaces the loose module-level functions from workflow_run_service.py.

All methods are @staticmethod with @sync_to_async — stateless DB access
grouped by responsibility: access, creation, and query.
"""

import logging
from typing import Optional
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.contrib.auth import get_user_model

from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep, StepNodeData
)
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.utils.constants import NodeType
from workflows.services.run_ordering import get_workflow_run_order_map
from workflows.services.run_status import RunStatusManager


User = get_user_model()
logger = logging.getLogger(__name__)

# Stale run thresholds (in minutes)
STALE_RUN_THRESHOLD_MINUTES = 30
STALE_PARTIAL_RUN_THRESHOLD_MINUTES = 120


class WorkflowRunRepository:
    """Single entry point for all WorkflowRun DB operations."""

    # ==================== Access ====================

    @staticmethod
    @sync_to_async
    def get_user(user_id: int) -> Optional[User]:
        """Fetch user from database by ID."""
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def validate_access(run_id: int, user) -> bool:
        """Validate that user has access to the workflow run."""
        try:
            run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
            return run.workflow.user_id == user.id
        except WorkflowRun.DoesNotExist:
            return False

    @staticmethod
    @sync_to_async
    def get_workflow_run(run_id: int, user) -> Optional[WorkflowRun]:
        """Get workflow run instance with access validation."""
        try:
            run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
            if run.workflow.user_id == user.id:
                return run
            return None
        except WorkflowRun.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_workflow(workflow_id: int, user) -> Optional[Workflow]:
        """Get workflow instance with access validation."""
        try:
            return Workflow.objects.get(id=workflow_id, user=user)
        except Workflow.DoesNotExist:
            return None

    # ==================== Creation ====================

    @staticmethod
    @sync_to_async
    def create_full_run(
        workflow_id: int,
        user,
        user_input: str = ''
    ) -> Optional[WorkflowRun]:
        """
        Create a new workflow run with WorkflowRunStep records for all step nodes.

        Returns WorkflowRun instance or None if creation failed.
        """
        try:
            workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
                id=workflow_id,
                user=user
            )

            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=user,
                is_partial=False
            )

            step_nodes = workflow.nodes.filter(
                node_type=NodeType.STEP
            ).select_related('data_content_type')
            order_by_node_id = get_workflow_run_order_map(workflow)

            for step_node in step_nodes:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=workflow_run,
                        step_node=step_node,
                        order=order_by_node_id.get(step_node.node_id, 0),
                        status=WorkflowRunStepStatus.PENDING
                    )

            RunStatusManager.recompute(workflow_run)

            return workflow_run

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to create workflow run: {str(e)}")
            return None

    @staticmethod
    @sync_to_async
    def create_partial_run(workflow_id: int, user) -> Optional[WorkflowRun]:
        """Create a new partial workflow run for manual mode execution."""
        try:
            workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
                id=workflow_id,
                user=user
            )

            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=user,
                is_partial=True
            )

            step_nodes = workflow.nodes.filter(
                node_type=NodeType.STEP
            ).select_related('data_content_type')
            order_by_node_id = get_workflow_run_order_map(workflow)

            for step_node in step_nodes:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=workflow_run,
                        step_node=step_node,
                        order=order_by_node_id.get(step_node.node_id, 0),
                        status=WorkflowRunStepStatus.PENDING
                    )

            RunStatusManager.recompute(workflow_run)

            return workflow_run

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to create partial workflow run: {str(e)}")
            return None

    @staticmethod
    @sync_to_async
    def convert_partial_to_full(
        partial_run: WorkflowRun,
        user_input: str = ''
    ) -> WorkflowRun:
        """Convert a partial run to a full run and create missing WorkflowRunStep objects."""
        partial_run.is_partial = False
        partial_run.save(update_fields=['is_partial'])

        workflow = partial_run.workflow
        existing_step_node_ids = set(
            WorkflowRunStep.objects.filter(workflow_run=partial_run)
            .values_list('step_node__node_id', flat=True)
        )

        step_nodes = workflow.nodes.filter(
            node_type='step'
        ).select_related('data_content_type')
        order_by_node_id = get_workflow_run_order_map(workflow)

        for step_node in step_nodes:
            if step_node.node_id not in existing_step_node_ids:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=partial_run,
                        step_node=step_node,
                        order=order_by_node_id.get(step_node.node_id, 0),
                        status=WorkflowRunStepStatus.PENDING
                    )

        RunStatusManager.recompute(partial_run)

        return partial_run

    # ==================== Query ====================

    @staticmethod
    @sync_to_async
    def get_existing_partial_run(workflow_id: int, user) -> Optional[WorkflowRun]:
        """
        Get existing incomplete partial run for a workflow.
        Also cleans up stale partial runs that have been stuck for too long.
        """
        partial_run = WorkflowRun.active_objects.filter(
            workflow_id=workflow_id,
            user=user,
            is_partial=True,
            ended_at__isnull=True
        ).order_by('-created_at').first()

        if partial_run and partial_run.status == WorkflowRunStepStatus.RUNNING:
            stale_threshold = timezone.now() - timedelta(
                minutes=STALE_PARTIAL_RUN_THRESHOLD_MINUTES
            )
            if partial_run.started_at and partial_run.started_at < stale_threshold:
                logger.warning(
                    f"Cleaning up stale partial run {partial_run.id} for workflow {workflow_id} "
                    f"(started at {partial_run.started_at})"
                )
                RunStatusManager.mark_failed(partial_run)
                partial_run.ended_at = timezone.now()
                partial_run.save(update_fields=['status', 'ended_at'])
                return None

        return partial_run

    @staticmethod
    @sync_to_async
    def get_run_for_status(run_id: int) -> Optional[WorkflowRun]:
        """Get a workflow run with prefetched data for status display."""
        try:
            return WorkflowRun.objects.prefetch_related(
                'steps__step_node'
            ).get(id=run_id)
        except WorkflowRun.DoesNotExist:
            return None

    @staticmethod
    @sync_to_async
    def get_latest_run(workflow_id: int, user) -> Optional[WorkflowRun]:
        """
        Get the latest workflow run for a workflow with prefetched data.
        Also cleans up stale runs stuck in "running" status.
        """
        try:
            workflow = Workflow.objects.get(id=workflow_id, user=user)

            latest_run = WorkflowRun.objects.filter(
                workflow=workflow
            ).prefetch_related(
                'steps__step_node'
            ).order_by('-created_at').first()

            if not latest_run:
                return None

            if latest_run.status == WorkflowRunStepStatus.RUNNING:
                stale_threshold = timezone.now() - timedelta(
                    minutes=STALE_RUN_THRESHOLD_MINUTES
                )
                if latest_run.started_at and latest_run.started_at < stale_threshold:
                    logger.warning(
                        f"Cleaning up stale run {latest_run.id} for workflow {workflow_id} "
                        f"(started at {latest_run.started_at})"
                    )
                    RunStatusManager.mark_failed(latest_run)
                    latest_run.ended_at = timezone.now()
                    latest_run.save(update_fields=['status', 'ended_at'])

            return latest_run

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to get latest workflow run: {str(e)}")
            return None
