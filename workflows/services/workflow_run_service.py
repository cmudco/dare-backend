"""
Workflow Run Database Service

Standalone @sync_to_async functions for workflow run database operations.
Extracted from WorkflowNamespace to improve modularity and testability.

All functions are stateless - they receive required parameters instead of
accessing class instance state. This follows the pattern established in
conversations/services/message_helpers/db_helpers.py.
"""

import logging
from typing import Optional, Dict, Any
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.contrib.auth import get_user_model

from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep, StepNodeData
)
from workflows.constants import WorkflowRunStepStatus
from workflows.api.serializers import WorkflowRunSerializer, WorkflowRunV2Serializer


User = get_user_model()
logger = logging.getLogger(__name__)

# Stale run thresholds (in minutes)
STALE_RUN_THRESHOLD_MINUTES = 30
STALE_PARTIAL_RUN_THRESHOLD_MINUTES = 120


@sync_to_async
def get_user(user_id: int) -> Optional[User]:
    """
    Fetch user from database by ID.

    Args:
        user_id: User ID to fetch

    Returns:
        User instance or None if not found
    """
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None


@sync_to_async
def validate_workflow_run_access(run_id: int, user) -> bool:
    """
    Validate that user has access to the workflow run.

    Args:
        run_id: Workflow run ID
        user: User instance

    Returns:
        True if user has access, False otherwise
    """
    try:
        run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
        return run.workflow.user_id == user.id
    except WorkflowRun.DoesNotExist:
        return False


@sync_to_async
def get_workflow_run(run_id: int, user) -> Optional[WorkflowRun]:
    """
    Get workflow run instance with access validation.

    Args:
        run_id: Workflow run ID
        user: User instance

    Returns:
        WorkflowRun instance or None if not found/unauthorized
    """
    try:
        run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
        if run.workflow.user_id == user.id:
            return run
        return None
    except WorkflowRun.DoesNotExist:
        return None


@sync_to_async
def get_workflow(workflow_id: int, user) -> Optional[Workflow]:
    """
    Get workflow instance with access validation.

    Args:
        workflow_id: Workflow ID
        user: User instance

    Returns:
        Workflow instance or None if not found/unauthorized
    """
    try:
        return Workflow.objects.get(id=workflow_id, user=user)
    except Workflow.DoesNotExist:
        return None


@sync_to_async
def create_workflow_run(
    workflow_id: int,
    user,
    user_input: str = ''
) -> Optional[WorkflowRun]:
    """
    Create a new workflow run with WorkflowRunStep records for all step nodes.

    Args:
        workflow_id: Workflow ID to create run for
        user: User instance
        user_input: Optional user input for the workflow

    Returns:
        WorkflowRun instance or None if creation failed
    """
    try:
        workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
            id=workflow_id,
            user=user
        )

        # Create the run
        workflow_run = WorkflowRun.objects.create(
            workflow=workflow,
            user=user,
            is_partial=False
        )

        # Note: user_input is passed to execution service, not stored on WorkflowRun
        # WorkflowRun model doesn't have a metadata field

        # Get step nodes and create WorkflowRunStep for each
        step_nodes = workflow.nodes.filter(
            node_type='step'
        ).select_related('data_content_type')

        for step_node in step_nodes:
            step_data = step_node.data_object
            if step_data and isinstance(step_data, StepNodeData):
                WorkflowRunStep.objects.create(
                    workflow_run=workflow_run,
                    step_node=step_node,
                    order=step_data.step_number if step_data.step_number else 0,
                    status=WorkflowRunStepStatus.PENDING
                )

        return workflow_run

    except Workflow.DoesNotExist:
        logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
        return None
    except Exception as e:
        logger.exception(f"Failed to create workflow run: {str(e)}")
        return None


@sync_to_async
def create_partial_workflow_run(workflow_id: int, user) -> Optional[WorkflowRun]:
    """
    Create a new partial workflow run for manual mode execution.

    Args:
        workflow_id: Workflow ID
        user: User instance

    Returns:
        WorkflowRun instance or None if creation failed
    """
    try:
        workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
            id=workflow_id,
            user=user
        )

        # Create a partial run
        workflow_run = WorkflowRun.objects.create(
            workflow=workflow,
            user=user,
            is_partial=True
        )

        # Get step nodes and create WorkflowRunStep for each
        step_nodes = workflow.nodes.filter(
            node_type='step'
        ).select_related('data_content_type')

        for step_node in step_nodes:
            step_data = step_node.data_object
            if step_data and isinstance(step_data, StepNodeData):
                WorkflowRunStep.objects.create(
                    workflow_run=workflow_run,
                    step_node=step_node,
                    order=step_data.step_number if step_data.step_number else 0,
                    status=WorkflowRunStepStatus.PENDING
                )

        return workflow_run

    except Workflow.DoesNotExist:
        logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
        return None
    except Exception as e:
        logger.exception(f"Failed to create partial workflow run: {str(e)}")
        return None


@sync_to_async
def get_existing_partial_run(workflow_id: int, user) -> Optional[WorkflowRun]:
    """
    Get existing incomplete partial run for a workflow.

    Also cleans up stale partial runs that have been stuck for too long.

    Args:
        workflow_id: Workflow ID
        user: User instance

    Returns:
        WorkflowRun instance or None if no partial run exists
    """
    partial_run = WorkflowRun.active_objects.filter(
        workflow_id=workflow_id,
        user=user,
        is_partial=True,
        ended_at__isnull=True
    ).order_by('-created_at').first()

    # Clean up stale partial runs
    if partial_run and partial_run.status == WorkflowRunStepStatus.RUNNING:
        stale_threshold = timezone.now() - timedelta(
            minutes=STALE_PARTIAL_RUN_THRESHOLD_MINUTES
        )
        if partial_run.started_at and partial_run.started_at < stale_threshold:
            logger.warning(
                f"Cleaning up stale partial run {partial_run.id} for workflow {workflow_id} "
                f"(started at {partial_run.started_at})"
            )
            partial_run.status = WorkflowRunStepStatus.FAILED
            partial_run.ended_at = timezone.now()
            partial_run.save(update_fields=['status', 'ended_at'])
            return None

    return partial_run


@sync_to_async
def convert_partial_to_full_run(
    partial_run: WorkflowRun,
    user_input: str = ''
) -> WorkflowRun:
    """
    Convert a partial run to a full run and create missing WorkflowRunStep objects.

    Args:
        partial_run: The partial WorkflowRun to convert
        user_input: Optional user input for the workflow

    Returns:
        The converted WorkflowRun instance
    """
    # Mark as non-partial since we're completing it in full mode
    partial_run.is_partial = False
    partial_run.save(update_fields=['is_partial'])

    # Create WorkflowRunStep objects for steps that haven't been created yet
    workflow = partial_run.workflow
    existing_step_node_ids = set(
        WorkflowRunStep.objects.filter(workflow_run=partial_run)
        .values_list('step_node__node_id', flat=True)
    )

    step_nodes = workflow.nodes.filter(
        node_type='step'
    ).select_related('data_content_type')

    for step_node in step_nodes:
        if step_node.node_id not in existing_step_node_ids:
            step_data = step_node.data_object
            if step_data and isinstance(step_data, StepNodeData):
                WorkflowRunStep.objects.create(
                    workflow_run=partial_run,
                    step_node=step_node,
                    order=step_data.step_number if step_data.step_number else 0,
                    status=WorkflowRunStepStatus.PENDING
                )

    return partial_run


@sync_to_async
def get_workflow_run_status(run_id: int) -> Optional[Dict[str, Any]]:
    """
    Get the current status of a workflow run.

    Uses V2 serializer for consistent data shape with socket events.
    Includes nodeStates and pendingValidation.

    Args:
        run_id: Workflow run ID

    Returns:
        Status dictionary or None if not found
    """
    try:
        run = WorkflowRun.objects.prefetch_related(
            'steps__step_node'
        ).get(id=run_id)

        # Use V2 serializer for consistent formatting with socket events
        serializer = WorkflowRunV2Serializer(run)
        return {
            'type': 'workflow_status',
            **serializer.data
        }
    except WorkflowRun.DoesNotExist:
        return None


@sync_to_async
def get_latest_workflow_run(workflow_id: int, user) -> Optional[Dict[str, Any]]:
    """
    Get the latest workflow run for a workflow with full execution state.

    Also cleans up stale runs that have been stuck in "running" status.

    Args:
        workflow_id: Workflow ID
        user: User instance

    Returns:
        Full run data with nodeStates or None if no runs exist
    """
    try:
        # Verify workflow access
        workflow = Workflow.objects.get(id=workflow_id, user=user)

        # Get the latest run
        latest_run = WorkflowRun.objects.filter(
            workflow=workflow
        ).prefetch_related(
            'steps__step_node'
        ).order_by('-created_at').first()

        if not latest_run:
            return None

        # Check for stale run and clean up
        if latest_run.status == WorkflowRunStepStatus.RUNNING:
            stale_threshold = timezone.now() - timedelta(
                minutes=STALE_RUN_THRESHOLD_MINUTES
            )
            # Check if the run started more than threshold ago
            if latest_run.started_at and latest_run.started_at < stale_threshold:
                logger.warning(
                    f"Cleaning up stale run {latest_run.id} for workflow {workflow_id} "
                    f"(started at {latest_run.started_at})"
                )
                latest_run.status = WorkflowRunStepStatus.FAILED
                latest_run.ended_at = timezone.now()
                latest_run.save(update_fields=['status', 'ended_at'])

        # Use V2 serializer for full nodeStates
        serializer = WorkflowRunV2Serializer(latest_run)
        return serializer.data

    except Workflow.DoesNotExist:
        logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
        return None
    except Exception as e:
        logger.exception(f"Failed to get latest workflow run: {str(e)}")
        return None
