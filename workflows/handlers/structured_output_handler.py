"""
Structured Output Node Handler for workflow execution.

This handler executes independent structured output nodes that can route workflow
execution based on AI evaluation with structured schema enforcement.
Extends BaseRoutingHandler for shared routing functionality.
"""
import logging
from typing import Optional, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.base_routing_handler import BaseRoutingHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import StructuredOutputNodeData
from workflows.constants import WorkflowRunStepStatus
from conversations.models import LLM

from workflows.handlers.utils import (
    NodeType,
    MetadataKey,
    ErrorResultBuilder,
    NodeDataValidator,
    InputValidator,
)


logger = logging.getLogger(__name__)


class StructuredOutputNodeHandler(BaseRoutingHandler):
    """
    Handler for 'structuredOutput' type nodes.

    This handler orchestrates independent structured output node execution by:
    1. Extracting input from text_input field OR previous node output
    2. Building base prompt with route selection instructions
    3. Optionally applying custom prompt template if configured
    4. Evaluating input using AI with structured output schema
    5. Handling human validation workflows when required

    The node operates independently with:
    - Optional text_input field for direct input
    - Optional prompt template (falls back to base routing prompt)
    - Routes configuration (name + description for UI)
    - LLM selection for routing decision
    - Human validation toggle
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process the given node type."""
        return node_type == NodeType.STRUCTURED_OUTPUT

    def _get_node_type_name(self) -> str:
        """Get the human-readable name for this node type."""
        return "structured output"

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a structured output node by evaluating input and selecting a route.

        Args:
            node: The structured output node to execute
            context: Execution context with previous results and workflow info

        Returns:
            NodeExecutionResult with routing decision and explanation, or pending human input status
        """
        start_time = timezone.now()
        correlation_id = f"structured-output-{node.id}"

        try:
            # Validate and get structured output configuration
            so_data = await self._get_node_data(node)
            if so_data is None:
                return ErrorResultBuilder.build_validation_error_result(
                    node_id=node.id,
                    node_type=NodeType.STRUCTURED_OUTPUT,
                    validation_message="Invalid or missing structured output node data"
                )

            # Get or create workflow run step
            step_number = await database_sync_to_async(
                lambda: so_data.step_number
            )()

            workflow_run_step = await self._get_or_create_workflow_run_step(
                context.workflow_run,
                node,
                step_number
            )

            # Update status to running
            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.RUNNING
            )

            logger.info(f"[{correlation_id}] Starting structured output node execution")

            # Get routes configuration
            routes = await self._get_routes_from_node_data(so_data)

            if not routes or len(routes) == 0:
                return ErrorResultBuilder.build_error_result(
                    Exception("No routes defined for structured output node"),
                    context={'node_id': node.id, 'node_type': NodeType.STRUCTURED_OUTPUT}
                )

            route_names = [r['name'] for r in routes]

            # Build routing prompt (includes input from text_input and/or previous node)
            message = await self._get_prompt_for_routing(so_data, routes, route_names)

            # Append previous node output if available
            input_text = await self._get_input_for_evaluation(node, context)
            if input_text:
                message = f"{message}\n\nPrevious Step Output:\n{input_text}"

            # Build structured output spec
            structured_spec = self._build_structured_output_spec(route_names)
            logger.info(
                f"[{correlation_id}] Built structured spec for routes: {route_names}"
            )

            # Get LLM
            llm = await self._get_llm_for_node(so_data)

            # Query LLM for routing decision using shared logic
            selected_route, analysis_text, token_usage = await self._query_llm_for_routing(
                message=message,
                llm=llm,
                routes=routes,
                route_names=route_names,
                structured_spec=structured_spec,
                workflow_run=context.workflow_run,
                correlation_id=correlation_id
            )

            # Process billing
            await self._process_routing_billing(
                so_data, context.workflow_run, node, token_usage
            )

            # Check if human validation is required
            require_human_validation = await database_sync_to_async(
                lambda: so_data.require_human_validation
            )()

            if require_human_validation:
                return await self._handle_human_validation_required(
                    workflow_run_step=workflow_run_step,
                    selected_route=selected_route,
                    analysis_text=analysis_text,
                    routes=routes,
                    node=node,
                    node_data=so_data,
                    start_time=start_time,
                    correlation_id=correlation_id
                )

            # No human validation required - proceed with AI decision
            metadata = await self._build_routing_metadata(
                selected_route=selected_route,
                analysis_text=analysis_text,
                routes=routes,
                is_human_validated=False
            )

            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.COMPLETED,
                response=selected_route,
                metadata=metadata
            )

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Successfully executed structured output node in {execution_time:.2f}s. "
                f"Selected route: {selected_route}"
            )

            return NodeExecutionResult(
                success=True,
                output=selected_route,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata=metadata
            )

        except Exception as e:
            logger.error(
                f"[{correlation_id}] Structured output node execution failed: {str(e)}",
                exc_info=True
            )

            result = self._build_error_result(e, node, start_time)

            # Update workflow run step with error
            try:
                workflow_run_step = await self._get_or_create_workflow_run_step(
                    context.workflow_run,
                    node
                )
                await self._update_step_status(
                    workflow_run_step,
                    WorkflowRunStepStatus.FAILED,
                    error=result.error
                )
            except Exception as update_error:
                logger.error(
                    f"[{correlation_id}] Failed to update step status: {str(update_error)}"
                )

            return result

    # ==================== Abstract Method Implementations ====================

    async def _get_node_data(self, node: ExecutionNode) -> Optional[StructuredOutputNodeData]:
        """Get and validate structured output node data."""
        so_data = await database_sync_to_async(
            lambda: node.db_node.data_object
        )()

        if not NodeDataValidator.validate_node_data_type(
            so_data, StructuredOutputNodeData, node.id
        ):
            return None

        return so_data

    async def _get_input_for_evaluation(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Get input from previous node output.

        StructuredOutputNode can take input from:
        1. text_input field (handled in _get_prompt_for_routing)
        2. Previous node output (returned here)

        Args:
            node: The structured output node
            context: Execution context with previous results

        Returns:
            Previous node output string, or None if not available
        """
        if not context.previous_results:
            return None

        # Get input from previous nodes using utility
        return InputValidator.get_input_from_results(
            context.previous_results,
            prefer_latest=True
        )

    async def _get_prompt_for_routing(
        self,
        node_data: StructuredOutputNodeData,
        routes: List[Dict],
        route_names: List[str]
    ) -> str:
        """
        Build the complete routing prompt for structured output node.

        Combines:
        1. Base routing prompt with available routes
        2. Optional custom prompt template (if configured)
        3. Optional text_input (if provided)

        Args:
            node_data: Structured output node configuration
            routes: List of route definitions
            route_names: List of route names

        Returns:
            Complete prompt string
        """
        # Base prompt for routing
        base_prompt = self._build_base_routing_prompt(route_names)

        # Get optional custom prompt
        prompt_obj = await database_sync_to_async(
            lambda: node_data.prompt
        )()
        custom_prompt = await database_sync_to_async(
            lambda: prompt_obj.content if prompt_obj else ""
        )()

        # Get optional text input
        text_input = await database_sync_to_async(
            lambda: node_data.text_input or ""
        )()

        # Build complete message
        message_parts = [base_prompt]

        if custom_prompt:
            message_parts.append(f"\n\nAdditional Context:\n{custom_prompt}")

        if text_input:
            message_parts.append(f"\n\nInput:\n{text_input}")

        return "\n".join(message_parts)

    def _build_base_routing_prompt(self, route_names: List[str]) -> str:
        """
        Build the base routing prompt for structured output.

        Args:
            route_names: List of available route names

        Returns:
            Base prompt string
        """
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
        """
        Get the LLM configured for this structured output node.

        Args:
            node_data: Structured output node configuration

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        llm = await database_sync_to_async(lambda: node_data.llm)()

        if llm:
            return llm

        return await self._get_default_llm()
