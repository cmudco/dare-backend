"""
Workflows' implementation of the tool-loop seam.

The same ``ToolLoopService`` that runs chat turns runs workflow steps; this
binding maps its host hooks onto workflow surfaces — ``WorkflowStepToolCall``
rows for persistence, ``WorkflowRunStep.context_trace``/``retrieval_trace``
for traces, and the workflow ``EventEmitter``'s ``step_streaming`` contract
for text. Tool lifecycle events go out through the raw send callback with
workflow correlation keys, byte-identical to chat's payloads otherwise.

Billing stays end-of-step (``_process_billing``): the mid-stream gate
always passes.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.constants import ToolCallOrigin
from core.services.llm_helpers.retrieval_targets import WorkflowRetrievalTarget
from core.services.tool_loop.persistence import serialize_persisted_result
from workflows.handlers.event_emitter import EventEmitter
from workflows.models import WorkflowRunStep, WorkflowStepToolCall

logger = logging.getLogger(__name__)


class WorkflowToolLoopStore:
    """Persists one workflow step's tool calls and context trace."""

    def __init__(self, run_step: WorkflowRunStep) -> None:
        self._run_step = run_step
        self.turn_key = f"ws{run_step.id}"
        # One target per step run: its once-per-turn trace reset must
        # survive across repeated search_documents calls in the same loop.
        self.retrieval_target = WorkflowRetrievalTarget(run_step)

    @database_sync_to_async
    def clear_prior_tool_calls(self) -> None:
        """Re-run of a step: drop its previous tool-call rows."""
        deleted, _ = WorkflowStepToolCall.objects.filter(
            workflow_run_step=self._run_step
        ).delete()
        if deleted:
            logger.info(
                "[WorkflowToolLoopStore] Cleared %s prior tool calls for "
                "re-run step %s",
                deleted,
                self._run_step.id,
            )

    @database_sync_to_async
    def save_context_trace(self, trace: Dict[str, Any]) -> None:
        self._run_step.context_trace = trace
        self._run_step.save(update_fields=["context_trace"])

    @database_sync_to_async
    def save_tool_call(
        self,
        *,
        call: Any,
        server_slug: str,
        origin: str,
        arguments: Dict[str, Any],
        raw_result: Dict[str, Any],
        is_error: bool,
        error: str,
        round_index: int,
        execution_time_ms: int,
    ) -> None:
        try:
            result_text = None
            if raw_result and not is_error:
                result_text = serialize_persisted_result(raw_result)
            WorkflowStepToolCall.objects.create(
                workflow_run_step=self._run_step,
                tool_call_id=call.id,
                server_slug=server_slug,
                origin=(
                    origin if origin in ToolCallOrigin.values else ToolCallOrigin.MCP
                ),
                tool_name=call.name,
                arguments=arguments,
                status="failed" if is_error else "completed",
                result=result_text,
                error=error or None,
                executed_at=timezone.now(),
                round_index=round_index,
                execution_time_ms=execution_time_ms,
            )
        except Exception as exc:
            logger.error("Failed to save WorkflowStepToolCall: %s", exc)


class WorkflowStreamSink:
    """Adapts the loop's accumulated text to step_streaming's delta contract."""

    def __init__(self, emitter: EventEmitter, node_id: str) -> None:
        self._emitter = emitter
        self._node_id = node_id
        self._sent_chars = 0
        self._chunks_sent = 0

    async def text(self, accumulated_text: str) -> None:
        delta = accumulated_text[self._sent_chars :]
        self._sent_chars = len(accumulated_text)
        if not delta:
            return
        self._chunks_sent += 1
        await self._emitter.step_streaming(self._node_id, delta, self._chunks_sent)

    async def thinking(self, accumulated_text: str, thinking_summary: str) -> None:
        """Workflow steps have no thinking UI; visible text flows via text()."""


class WorkflowBillingGate:
    """Workflows bill once at end of step; the mid-stream gate always passes."""

    async def check(
        self, usage_totals: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return True, None


class WorkflowToolLoopBinding:
    """Bundles the workflow implementations of the tool-loop seam."""

    def __init__(
        self,
        *,
        run_step: WorkflowRunStep,
        node_id: str,
        user: Any,
        emitter: EventEmitter,
        send_callback: Any,
    ) -> None:
        # Chat-only context: tools that require a Message/Conversation
        # (artifacts, MCP) error cleanly inside the execution service.
        self.message = None
        self.conversation = None
        self.user = user
        self.send_callback = send_callback
        self.correlation = {
            "workflow_run_id": run_step.workflow_run_id,
            "node_id": node_id,
            "run_step_id": run_step.id,
        }
        self.store = WorkflowToolLoopStore(run_step)
        self.sink = WorkflowStreamSink(emitter, node_id)
        self.gate = WorkflowBillingGate()
