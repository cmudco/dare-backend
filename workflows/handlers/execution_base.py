"""
Base execution handler with common billing, status updates, and streaming.

Shared by handlers that execute LLM calls (step_handler, structured_output_handler).
"""
import logging
from typing import Dict, Optional

from channels.db import database_sync_to_async
from django.utils import timezone

from billing.exceptions import PaymentRequiredError
from conversations.models import LLM
from core.services.billing_service import BillingService
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.event_emitter import EventEmitter
from workflows.models import WorkflowRun, WorkflowRunStep
from workflows.services.run_ordering import get_workflow_run_order_map
from workflows.services.run_status import RunStatusManager


logger = logging.getLogger(__name__)


class BaseExecutionHandler(BaseNodeHandler):
    """
    Base handler for nodes that execute LLM calls.

    Provides:
    - Workflow run step creation/retrieval
    - Status management (with RunStatusManager cascade)
    - Streaming with chunk persistence for reconnect resilience
    - Billing processing (non-blocking — failures don't fail execution)
    - Standardized error result building
    """

    # ==================== Run Step Management ====================

    async def _get_or_create_workflow_run_step(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        order: Optional[int] = None,
        reset_if_exists: bool = False,
    ) -> WorkflowRunStep:
        """Get or create a WorkflowRunStep. Resets it if reset_if_exists (manual re-run)."""

        def _get_or_create():
            persisted_order = order
            if persisted_order is None:
                persisted_order = get_workflow_run_order_map(workflow_run.workflow).get(node.id, 0)
            step, created = WorkflowRunStep.objects.get_or_create(
                workflow_run=workflow_run,
                step_node=node.db_node,
                defaults={
                    'order': persisted_order,
                    'status': WorkflowRunStepStatus.PENDING,
                },
            )
            if not created and reset_if_exists:
                step.status = WorkflowRunStepStatus.PENDING
                step.response = None
                step.error = None
                step.started_at = None
                step.save(update_fields=['status', 'response', 'error', 'started_at'])
                RunStatusManager.recompute(workflow_run)
            return step

        return await database_sync_to_async(_get_or_create)()

    # ==================== Status Updates ====================

    async def _update_step_status(
        self,
        workflow_run_step: WorkflowRunStep,
        status: str,
        response: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        """
        Atomically update step status/response/error/metadata.

        Returns started_at timestamp when transitioning to RUNNING.
        """

        def _update():
            update_kwargs = {'status': status}
            started_at_value = None

            if status == WorkflowRunStepStatus.RUNNING:
                if workflow_run_step.started_at is None:
                    started_at_value = timezone.now()
                    update_kwargs['started_at'] = started_at_value
                else:
                    started_at_value = workflow_run_step.started_at
                # Clear stale output before a new stream starts
                update_kwargs['response'] = ''
                update_kwargs['error'] = None

            if response is not None:
                update_kwargs['response'] = response

            if error is not None:
                update_kwargs['error'] = error

            if metadata is not None:
                step = WorkflowRunStep.objects.get(id=workflow_run_step.id)
                existing_metadata = step.metadata or {}
                existing_metadata.update(metadata)
                update_kwargs['metadata'] = existing_metadata

            WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(**update_kwargs)
            RunStatusManager.recompute(workflow_run_step.workflow_run_id)
            return started_at_value

        return await database_sync_to_async(_update)()

    # ==================== Streaming ====================

    async def _execute_llm_query_with_collection(
        self,
        llm_query_generator,
        emitter: EventEmitter,
        node_id: Optional[str] = None,
    ) -> tuple[str, Dict]:
        """
        Consume an LLM async generator, collecting the full response and streaming
        chunks to the frontend via EventEmitter.

        Returns (full_response, token_usage).
        """
        full_response = ""
        token_usage = {}
        accumulated_tokens = 0

        async for chunk, usage in llm_query_generator:
            if chunk:
                full_response += chunk
                accumulated_tokens += 1

                if node_id:
                    await emitter.step_streaming(node_id, chunk, accumulated_tokens)

            if usage:
                token_usage = usage

        return full_response, token_usage

    # ==================== Billing ====================

    async def _process_billing(
        self,
        token_usage: Dict,
        llm: LLM,
        user,
        step_node_id: Optional[int] = None,
    ) -> bool:
        """Process billing for token usage. Returns False on failure (non-blocking)."""
        if not token_usage or not token_usage.get('input_tokens') or not token_usage.get('output_tokens'):
            return True

        try:
            billing_service = BillingService()
            await database_sync_to_async(
                billing_service.process_workflow_billing
            )(
                user=user,
                llm=llm,
                input_tokens=token_usage['input_tokens'],
                output_tokens=token_usage['output_tokens'],
                step_node_id=step_node_id,
            )
            return True

        except PaymentRequiredError as billing_error:
            # Wallet didn't cover the workflow step. The LLM has already run
            # (we have token counts), so the cost is unrecoverable — log
            # loudly so audits can spot it. The step itself is reported as
            # billing-failed; the workflow caller decides whether to halt.
            logger.error(
                "Workflow billing failed (unrecoverable): step_node_id=%s "
                "code=%s details=%s",
                step_node_id, billing_error.code, billing_error.details,
            )
            return False
        except Exception as billing_error:
            logger.error(
                f"Billing error for step_node_id={step_node_id}: {billing_error}",
                exc_info=True,
            )
            return False

    # ==================== Helpers ====================

    async def _get_user_from_workflow_run(self, workflow_run: WorkflowRun):
        """Get user from workflow run."""
        return await database_sync_to_async(lambda: workflow_run.workflow.user)()

    def _build_error_result(
        self,
        exception: Exception,
        node: ExecutionNode,
        start_time,
        custom_message: Optional[str] = None,
    ) -> NodeExecutionResult:
        """Build a standardized error NodeExecutionResult."""
        error_category, error_type = categorize_error(exception)
        error_msg = custom_message or f"{error_category}: {exception}"

        logger.error(
            f"{error_category} in node {node.id} ({error_type}): {exception}",
            exc_info=True,
        )

        execution_time = (timezone.now() - start_time).total_seconds()

        return NodeExecutionResult(
            success=False,
            error=error_msg,
            execution_time=execution_time,
        )
