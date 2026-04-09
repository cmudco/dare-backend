"""
Structured Output Node Handler for workflow execution.

Executes independent structured output nodes that route workflow execution
based on AI evaluation with structured schema enforcement.

Pipeline: validate → init → start → build prompt → query LLM → bill → complete/pause
"""
import logging
from typing import Dict, List, Optional

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.models import LLM
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.handlers.base_routing_handler import BaseRoutingHandler
from workflows.handlers.event_emitter import EventEmitter
from workflows.handlers.utils import (
    ErrorResultBuilder,
    InputValidator,
    NodeDataValidator,
    NodeType,
)
from workflows.models import StructuredOutputNodeData


logger = logging.getLogger(__name__)


class StructuredOutputNodeHandler(BaseRoutingHandler):
    """Handler for 'structuredOutput' type nodes."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == NodeType.STRUCTURED_OUTPUT

    def _get_node_type_name(self) -> str:
        return "structured output"

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """
        Execute a structured output node as a clean pipeline:
        validate → init → start → build prompt → query LLM → bill → complete/pause
        """
        start_time = timezone.now()
        correlation_id = f"structured-output-{node.id}"
        emitter = EventEmitter(context.send_callback, workflow_run_id=context.workflow_run.id)

        try:
            # Validate
            so_data = await self._get_node_data(node)
            if so_data is None:
                return ErrorResultBuilder.build_validation_error_result(
                    node_id=node.id,
                    node_type=NodeType.STRUCTURED_OUTPUT,
                    validation_message="Invalid or missing structured output node data",
                )

            # Init run step
            run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)
            started_at = await self._update_step_status(run_step, WorkflowRunStepStatus.RUNNING)

            logger.info(f"[{correlation_id}] Starting structured output node execution")
            await emitter.step_started(node.id, node.label, NodeType.STRUCTURED_OUTPUT, started_at)

            # Build routing prompt
            routes = await self._get_routes_from_node_data(so_data)
            if not routes:
                return ErrorResultBuilder.build_error_result(
                    Exception("No routes defined for structured output node"),
                    context={'node_id': node.id, 'node_type': NodeType.STRUCTURED_OUTPUT},
                )

            route_names = [r['name'] for r in routes]
            message = await self._get_prompt_for_routing(so_data, routes, route_names)

            input_text = await self._get_input_for_evaluation(node, context)
            if input_text:
                message = f"{message}\n\nPrevious Step Output:\n{input_text}"

            # Query LLM
            structured_spec = self._build_structured_output_spec(route_names)
            llm = await self._get_llm_for_node(so_data)

            selected_route, analysis_text, token_usage = await self._query_llm_for_routing(
                message=message,
                llm=llm,
                routes=routes,
                route_names=route_names,
                structured_spec=structured_spec,
                workflow_run=context.workflow_run,
                correlation_id=correlation_id,
                emitter=emitter,
                node_id=node.id,
                workflow_run_step=run_step,
            )

            # Bill
            await self._process_routing_billing(so_data, context.workflow_run, node, token_usage)

            # Check human validation
            require_human_validation = await database_sync_to_async(
                lambda: so_data.require_human_validation
            )()

            if require_human_validation:
                return await self._handle_human_validation_required(
                    workflow_run_step=run_step,
                    selected_route=selected_route,
                    analysis_text=analysis_text,
                    routes=routes,
                    node=node,
                    node_data=so_data,
                    start_time=start_time,
                    correlation_id=correlation_id,
                )

            # Complete
            metadata = await self._build_routing_metadata(
                selected_route=selected_route,
                analysis_text=analysis_text,
                routes=routes,
                is_human_validated=False,
            )

            await self._update_step_status(
                run_step,
                WorkflowRunStepStatus.COMPLETED,
                response=selected_route,
                metadata=metadata,
            )

            execution_time = (timezone.now() - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Completed in {execution_time:.2f}s. "
                f"Selected route: {selected_route}"
            )

            return NodeExecutionResult(
                success=True,
                output=selected_route,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"[{correlation_id}] Failed: {e}", exc_info=True)
            await self._handle_failure(context, node, e, correlation_id)
            return self._build_error_result(e, node, start_time)

    # ==================== Failure Handling ====================

    async def _handle_failure(
        self,
        context: NodeExecutionContext,
        node: ExecutionNode,
        exception: Exception,
        correlation_id: str,
    ) -> None:
        """Update the run step to FAILED status after an error."""
        try:
            run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)
            error_msg = f"{type(exception).__name__}: {exception}"
            await self._update_step_status(
                run_step, WorkflowRunStepStatus.FAILED, error=error_msg,
            )
        except Exception as update_error:
            logger.error(f"[{correlation_id}] Failed to update step status: {update_error}")

    # ==================== Abstract Method Implementations ====================

    async def _get_node_data(self, node: ExecutionNode) -> Optional[StructuredOutputNodeData]:
        """Get and validate structured output node data."""
        so_data = await database_sync_to_async(lambda: node.db_node.data_object)()

        if not NodeDataValidator.validate_node_data_type(
            so_data, StructuredOutputNodeData, node.id,
        ):
            return None

        return so_data

    async def _get_input_for_evaluation(
        self, node: ExecutionNode, context: NodeExecutionContext,
    ) -> Optional[str]:
        """Get input from previous node output."""
        if not context.previous_results:
            return None

        return InputValidator.get_input_from_results(
            context.previous_results, prefer_latest=True,
        )

    async def _get_prompt_for_routing(
        self,
        node_data: StructuredOutputNodeData,
        routes: List[Dict],
        route_names: List[str],
    ) -> str:
        """Build the complete routing prompt."""
        base_prompt = self._build_base_routing_prompt(route_names)

        prompt_obj = await database_sync_to_async(lambda: node_data.prompt)()
        custom_prompt = await database_sync_to_async(
            lambda: prompt_obj.content if prompt_obj else ""
        )()

        text_input = await database_sync_to_async(lambda: node_data.text_input or "")()

        message_parts = [base_prompt]

        if custom_prompt:
            message_parts.append(f"\n\nAdditional Context:\n{custom_prompt}")

        if text_input:
            message_parts.append(f"\n\nInput:\n{text_input}")

        return "\n".join(message_parts)

    def _build_base_routing_prompt(self, route_names: List[str]) -> str:
        """Build the base routing prompt with available routes."""
        routes_list = ', '.join(route_names)
        return (
            f"You are a routing decision maker. Analyze the context provided and "
            f"select the most appropriate route. You MUST provide both a route selection "
            f"AND a brief analysis explaining your reasoning.\n\n"
            f"Available routes: {routes_list}\n\n"
            f"IMPORTANT: Your response MUST include:\n"
            f"1. Selected route (exactly one of: {routes_list})\n"
            f"2. Analysis (1-2 sentences explaining WHY you chose this route based on "
            f"the context, prompt configuration, or routing criteria - never leave this empty)"
        )

    async def _get_llm_for_node(self, node_data: StructuredOutputNodeData) -> LLM:
        """Get the LLM configured for this node, or fall back to default."""
        llm = await database_sync_to_async(lambda: node_data.llm)()

        if llm:
            return llm

        return await self._get_default_llm()
