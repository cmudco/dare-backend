import logging
import asyncio
import os
from django_rq import job
from django.utils import timezone

from .models import WorkflowRun
from core.services.workflow_execution_service import WorkflowExecutionService

from core.services.workflow_execution_service import WorkflowExecutionService

# Fix for macOS RQ worker crash with httpx/requests
# See: https://stackoverflow.com/questions/77132356/rq-job-terminated-unexpectedly
os.environ['no_proxy'] = '*'


@job('default', timeout=600)
def execute_workflow_run(workflow_run_id):
    """Execute workflow using new graph-based execution engine."""
    logger = logging.getLogger(__name__)

    try:
        workflow_run = WorkflowRun.active_objects.get(id=workflow_run_id)
    except WorkflowRun.DoesNotExist:
        logger.error(f"Workflow run {workflow_run_id} not found")
        return

    try:
        logger.info(f"Starting graph-based execution for workflow run {workflow_run_id}")

        service = WorkflowExecutionService()
        execution_result = asyncio.run(service.execute_workflow(workflow_run))

        if execution_result.get('pending_human_input'):
            logger.info(f"Workflow run {workflow_run_id} paused - waiting for human validation")
        elif execution_result['success']:
            logger.info(f"Workflow run {workflow_run_id} completed successfully")
        else:
            logger.error(f"Workflow run {workflow_run_id} failed: {execution_result.get('error', 'Unknown error')}")

    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
        try:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])
        except:
            pass


@job('default', timeout=600)
def resume_workflow_run(workflow_run_id, node_id, chosen_route):
    """Resume workflow execution after human validation."""
    logger = logging.getLogger(__name__)

    try:
        workflow_run = WorkflowRun.active_objects.get(id=workflow_run_id)
    except WorkflowRun.DoesNotExist:
        logger.error(f"Workflow run {workflow_run_id} not found")
        return

    try:
        logger.info(f"Resuming workflow run {workflow_run_id} from node {node_id} with route: {chosen_route}")

        service = WorkflowExecutionService()
        execution_result = asyncio.run(
            service.resume_workflow_after_human_validation(workflow_run, node_id, chosen_route)
        )

        if execution_result.get('pending_human_input'):
            logger.info(f"Workflow run {workflow_run_id} paused again - waiting for more human validation")
        elif execution_result['success']:
            logger.info(f"Workflow run {workflow_run_id} completed successfully after resumption")
        else:
            logger.error(f"Workflow run {workflow_run_id} failed after resumption: {execution_result.get('error', 'Unknown error')}")

    except Exception as e:
        logger.error(f"Workflow resume failed: {str(e)}", exc_info=True)
        try:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])
        except:
            pass
