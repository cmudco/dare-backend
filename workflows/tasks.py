import logging
from django_rq import job
from django.utils import timezone

from .models import WorkflowRun
from core.services.workflow_execution_service import WorkflowExecutionService


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
        # Use the new graph-based execution service
        logger.info(f"Starting graph-based execution for workflow run {workflow_run_id}")

        # Run the async execution in a new event loop
        def run_workflow_execution():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                service = WorkflowExecutionService()
                result = loop.run_until_complete(service.execute_workflow(workflow_run))
                return result
            finally:
                loop.close()

        execution_result = run_workflow_execution()

        if execution_result['success']:
            logger.info(f"Workflow run {workflow_run_id} completed successfully")
        else:
            logger.error(f"Workflow run {workflow_run_id} failed: {execution_result.get('error', 'Unknown error')}")

    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
        # Mark workflow as failed
        try:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])
        except:
            pass