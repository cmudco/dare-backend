"""
Base execution handler with common billing, status updates, and error handling.

This module provides shared functionality for handlers that execute LLM calls
and manage workflow run steps (step_handler, structured_output_handler).
"""
import logging
from typing import Dict, Optional
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.base import BaseNodeHandler, ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowRun, WorkflowRunStep
from workflows.constants import WorkflowRunStepStatus
from core.services.billing_service import BillingService
from conversations.models import LLM


logger = logging.getLogger(__name__)


class BaseExecutionHandler(BaseNodeHandler):
    """
    Base handler for nodes that execute LLM calls.
    
    Provides common patterns for:
    - Workflow run step creation/retrieval
    - Status management
    - Billing processing
    - Token usage collection
    - Error handling
    """

    async def _get_or_create_workflow_run_step(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        step_number: Optional[int] = None
    ) -> WorkflowRunStep:
        """
        Get or create a WorkflowRunStep for the node.

        Args:
            workflow_run: The workflow run instance
            node: The execution node
            step_number: Optional step number for ordering

        Returns:
            WorkflowRunStep instance
        """
        def _get_or_create():
            order = step_number if step_number is not None else (node.step_number or 0)
            step, created = WorkflowRunStep.objects.get_or_create(
                workflow_run=workflow_run,
                step_node=node.db_node,
                defaults={
                    'order': order,
                    'status': WorkflowRunStepStatus.PENDING
                }
            )
            return step

        return await database_sync_to_async(_get_or_create)()

    async def _update_step_status(
        self,
        workflow_run_step: WorkflowRunStep,
        status: str,
        response: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """
        Update workflow run step status and optional fields.

        Args:
            workflow_run_step: Workflow run step to update
            status: New status
            response: Optional response text
            error: Optional error message
            metadata: Optional metadata dictionary
        """
        def _update():
            update_kwargs = {'status': status}
            
            if response is not None:
                update_kwargs['response'] = response
            
            if error is not None:
                update_kwargs['error'] = error
            
            if metadata is not None:
                # Merge with existing metadata
                step = WorkflowRunStep.objects.get(id=workflow_run_step.id)
                existing_metadata = step.metadata or {}
                existing_metadata.update(metadata)
                update_kwargs['metadata'] = existing_metadata
            
            WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(**update_kwargs)

        await database_sync_to_async(_update)()

    async def _process_billing(
        self,
        token_usage: Dict,
        llm: LLM,
        user,
        step_node_id: Optional[int] = None
    ) -> bool:
        """
        Process billing for token usage.

        Args:
            token_usage: Token usage statistics
            llm: LLM instance used
            user: User to bill
            step_node_id: Optional step node ID for tracking

        Returns:
            bool: True if billing succeeded, False otherwise
        """
        if not token_usage or not token_usage.get('input_tokens') or not token_usage.get('output_tokens'):
            logger.debug("No token usage data available for billing")
            return True

        try:
            billing_service = BillingService()

            billing_success = await database_sync_to_async(
                billing_service.process_workflow_billing
            )(
                user=user,
                llm=llm,
                input_tokens=token_usage['input_tokens'],
                output_tokens=token_usage['output_tokens'],
                step_node_id=step_node_id
            )

            if not billing_success:
                logger.warning(
                    f"Billing failed for step_node_id={step_node_id}, but continuing execution"
                )
                return False

            return True

        except Exception as billing_error:
            logger.error(
                f"Billing error for step_node_id={step_node_id}: {str(billing_error)}",
                exc_info=True
            )
            # Continue execution even if billing fails
            return False

    async def _execute_llm_query_with_collection(
        self,
        llm_query_generator,
    ) -> tuple[str, Dict]:
        """
        Execute LLM query and collect full response with token usage.

        Args:
            llm_query_generator: Async generator from llm_service.query()

        Returns:
            tuple: (full_response, token_usage)
        """
        full_response = ""
        token_usage = {}

        async for chunk, usage in llm_query_generator:
            if chunk:
                full_response += chunk
            if usage:
                token_usage = usage

        return full_response, token_usage

    async def _get_user_from_workflow_run(self, workflow_run: WorkflowRun):
        """
        Get user from workflow run.

        Args:
            workflow_run: Workflow run instance

        Returns:
            User instance
        """
        return await database_sync_to_async(
            lambda: workflow_run.workflow.user
        )()

    def _build_error_result(
        self,
        exception: Exception,
        node: ExecutionNode,
        start_time,
        custom_message: Optional[str] = None
    ) -> NodeExecutionResult:
        """
        Build a NodeExecutionResult for an error.

        Args:
            exception: The exception that occurred
            node: Execution node
            start_time: Execution start time
            custom_message: Optional custom error message

        Returns:
            NodeExecutionResult with error information
        """
        from workflows.handlers.base import categorize_error
        
        error_category, error_type = categorize_error(exception)
        error_msg = custom_message or f"{error_category}: {str(exception)}"
        
        logger.error(
            f"{error_category} in node {node.id} ({error_type}): {str(exception)}",
            exc_info=True
        )

        end_time = timezone.now()
        execution_time = (end_time - start_time).total_seconds()

        return NodeExecutionResult(
            success=False,
            error=error_msg,
            execution_time=execution_time
        )
