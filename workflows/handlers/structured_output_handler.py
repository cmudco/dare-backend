"""
Structured Output Node Handler for workflow execution.

This handler executes independent structured output nodes that can route workflow
execution based on AI evaluation with structured schema enforcement.

The structured output node is fully independent and can be placed anywhere in the workflow,
taking input from previous nodes and routing to multiple paths based on configured routes.
"""
import logging
from typing import Optional, Tuple, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep, StructuredOutputNodeData
from workflows.constants import WorkflowRunStepStatus
from conversations.models import LLM
from core.services.llm_utils import SchemaTransformer
from core.services.dtos import LLMQueryRequestBuilder

# Import utility modules
from workflows.handlers.utils import (
    NodeType,
    LLMDefaults,
    ErrorCode,
    MetadataKey,
    ErrorResultBuilder,
    NodeDataValidator,
    InputValidator,
    RouteNormalizer,
    StructuredOutputBuilder,
    RouteInstructionBuilder,
)


logger = logging.getLogger(__name__)


class StructuredOutputNodeHandler(BaseExecutionHandler):
    """
    Handler for 'structuredOutput' type nodes.

    This handler orchestrates independent structured output node execution by:
    1. Validating node configuration and extracting input
    2. Building base prompt with route selection instructions
    3. Optionally applying custom prompt template if configured
    4. Evaluating input using AI with structured output schema
    5. Normalizing responses to match allowed routes
    6. Handling human validation workflows when required
    7. Processing billing and status updates via base handler

    The node operates independently with:
    - Optional text_input field for direct input
    - Optional prompt template (falls back to base routing prompt)
    - Routes configuration (name + description for UI, name used for schema)
    - LLM selection for routing decision
    - Human validation toggle
    """

    def can_handle(self, node_type: str) -> bool:
        """
        Check if this handler can process the given node type.

        Args:
            node_type: The type of node to check

        Returns:
            True if node_type is 'structuredOutput', False otherwise
        """
        return node_type == NodeType.STRUCTURED_OUTPUT

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a structured output node by evaluating input and selecting a route.

        This method orchestrates the complete structured output execution workflow including
        input preparation, prompt building, AI evaluation with schema enforcement,
        route normalization, human validation handling, billing, and result processing.

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
            so_data = await self._get_and_validate_so_data(node)
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
            routes = await database_sync_to_async(
                lambda: so_data.get_routes()
            )()

            if not routes or len(routes) == 0:
                return ErrorResultBuilder.build_error_result(
                    Exception("No routes defined for structured output node"),
                    context={'node_id': node.id, 'node_type': NodeType.STRUCTURED_OUTPUT}
                )

            # Extract route names for schema
            route_names = [r['name'] for r in routes]

            # Prepare input message
            message = await self._prepare_message_for_so_node(
                so_data, context, route_names
            )

            # Build structured output spec
            structured_spec = StructuredOutputBuilder.build_structured_spec(
                route_names
            )
            logger.info(
                f"[{correlation_id}] Built structured spec for routes: {route_names}, "
                f"spec: {structured_spec}"
            )

            # Get LLM and check provider capabilities
            llm = await self._get_llm_for_so_node(so_data)
            llm_provider = await database_sync_to_async(lambda: llm.provider)()
            llm_identifier = await database_sync_to_async(lambda: llm.identifier)()

            # Check if provider supports native structured output
            supports_native = SchemaTransformer.supports_native_structured_output(llm_provider)
            logger.info(
                f"[{correlation_id}] Using LLM: {llm_provider}/{llm_identifier}, "
                f"Native structured output support: {supports_native}"
            )

            # If provider doesn't support native structured output, append instructions
            final_message = message
            if not supports_native:
                instruction = RouteInstructionBuilder.build_simple_instruction(
                    route_names
                )
                final_message = f"{message}{instruction}"
                logger.info(
                    f"[{correlation_id}] Provider {llm_provider} doesn't support native structured output, "
                    "added XML instructions to message"
                )
            else:
                logger.info(
                    f"[{correlation_id}] Provider {llm_provider} supports native structured output, "
                    "will use native API"
                )

            # Execute LLM query with structured spec
            raw_response, explanation, token_usage = await self._execute_routing_query(
                so_data,
                final_message,
                context.workflow_run,
                structured_spec,
                llm,
                correlation_id
            )

            # Normalize response to match allowed routes
            selected_route = await self._normalize_route_response(
                raw_response,
                route_names,
                node.id
            )

            # Process billing using base handler
            await self._process_so_billing(
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
                    explanation=explanation,
                    routes=routes,
                    route_names=route_names,
                    raw_response=raw_response,
                    node=node,
                    so_data=so_data,
                    start_time=start_time,
                    correlation_id=correlation_id
                )

            # No human validation required - proceed with AI decision
            # NOTE: All keys use MetadataKey constants for consistency
            metadata = {
                MetadataKey.SELECTED_ROUTE: selected_route,
                MetadataKey.ANALYSIS: explanation,  # Unified key
                MetadataKey.AVAILABLE_ROUTES: routes,  # Full route objects [{name, description}]
                MetadataKey.IS_HUMAN_VALIDATED: False,
                MetadataKey.RAW_RESPONSE: raw_response if raw_response != selected_route else None,
            }

            # Response should only be the selected route name
            # Explanation is stored in metadata for display purposes
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
                output=selected_route,  # Only the route name, explanation in metadata
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

    # ==================== Private Helper Methods ====================

    async def _get_and_validate_so_data(
        self, node: ExecutionNode
    ) -> Optional[StructuredOutputNodeData]:
        """
        Get and validate structured output node data.

        Args:
            node: The execution node

        Returns:
            StructuredOutputNodeData if valid, None otherwise
        """
        so_data = await database_sync_to_async(
            lambda: node.db_node.data_object
        )()

        if not NodeDataValidator.validate_node_data_type(
            so_data, StructuredOutputNodeData, node.id
        ):
            return None

        return so_data

    async def _prepare_message_for_so_node(
        self,
        so_data: StructuredOutputNodeData,
        context: NodeExecutionContext,
        route_names: List[str]
    ) -> str:
        """
        Prepare the complete message for the structured output node.

        Combines:
        1. Base routing prompt (always included)
        2. Optional custom prompt template (if configured)
        3. Optional text_input (if provided)
        4. Previous step output (from context)

        Args:
            so_data: Structured output node configuration
            context: Execution context
            route_names: List of available route names

        Returns:
            Complete message string
        """
        # Base prompt for routing
        base_prompt = self._get_base_routing_prompt(route_names)

        # Get optional custom prompt
        prompt_obj = await database_sync_to_async(
            lambda: so_data.prompt
        )()
        custom_prompt = await database_sync_to_async(
            lambda: prompt_obj.content if prompt_obj else ""
        )()

        # Get optional text input
        text_input = await database_sync_to_async(
            lambda: so_data.text_input or ""
        )()

        # Get previous step output using utility
        previous_output = ""
        if context.previous_results:
            previous_output = InputValidator.get_input_from_results(
                context.previous_results,
                prefer_latest=True
            ) or ""

        # Build complete message
        message_parts = [base_prompt]

        if custom_prompt:
            message_parts.append(f"\n\nAdditional Context:\n{custom_prompt}")

        if text_input:
            message_parts.append(f"\n\nInput:\n{text_input}")

        if previous_output:
            message_parts.append(f"\n\nPrevious Step Output:\n{previous_output}")

        return "\n".join(message_parts)

    def _get_base_routing_prompt(self, route_names: List[str]) -> str:
        """
        Get the base routing prompt for structured output.

        Args:
            route_names: List of available route names

        Returns:
            Base prompt string
        """
        routes_list = ', '.join(route_names)
        return (
            f"You are a routing decision maker. Given the context and available routes, "
            f"select the most appropriate route and explain your reasoning.\n\n"
            f"Available routes: {routes_list}\n\n"
            f"Your response must include:\n"
            f"1. Selected route (must be exactly one of: {routes_list})\n"
            f"2. Brief explanation for your choice"
        )

    async def _execute_routing_query(
        self,
        so_data: StructuredOutputNodeData,
        message: str,
        workflow_run: WorkflowRun,
        structured_spec: Optional[Dict],
        llm: LLM,
        correlation_id: str
    ) -> Tuple[str, Optional[str], Optional[Dict]]:
        """
        Execute LLM query for routing decision with structured output.

        Args:
            so_data: Structured output node configuration
            message: Prepared message
            workflow_run: Current workflow run
            structured_spec: Structured output specification
            llm: LLM to use
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (raw_response, explanation, token_usage)
        """
        logger.debug(f"[{correlation_id}] Evaluating routing with LLM: {llm.identifier}")

        # Get user for LLM query
        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        # Build request with structured spec
        request = LLMQueryRequestBuilder.from_workflow_data(
            message=message,
            user=user,
            llm=llm,
            max_tokens=LLMDefaults.CONDITIONAL_MAX_TOKENS,
            temperature=LLMDefaults.CONDITIONAL_TEMPERATURE,
            structured_spec=structured_spec
        )

        response_generator = self.llm_service.query(request)

        # Collect response using base handler
        full_response, token_usage = await self._execute_llm_query_with_collection(
            response_generator
        )

        logger.debug(f"[{correlation_id}] LLM response received, parsing routing decision")

        # Try to extract explanation from response
        explanation = self._extract_explanation(full_response)

        return full_response, explanation, token_usage

    def _extract_explanation(self, response: str) -> Optional[str]:
        """
        Extract explanation from LLM response.

        For object schemas with explanation field, parses JSON.
        For other formats, tries to find explanation in text.

        Args:
            response: Full LLM response

        Returns:
            Explanation text or None
        """
        # Try to parse as JSON (for structured outputs with explanation field)
        try:
            import json
            data = json.loads(response)
            if isinstance(data, dict) and 'explanation' in data:
                explanation = data.get('explanation')
                if explanation:
                    logger.debug(f"Extracted explanation from JSON: {explanation}")
                    return str(explanation)
        except (json.JSONDecodeError, ValueError):
            # Not JSON, try other extraction methods
            pass

        # For non-structured or legacy formats, try multi-line extraction
        lines = response.strip().split('\n')
        if len(lines) > 1:
            # Join all lines after the first as explanation
            return '\n'.join(lines[1:]).strip()

        return None

    async def _normalize_route_response(
        self,
        raw_response: str,
        allowed_routes: List[str],
        node_id: str
    ) -> str:
        """
        Normalize LLM response to match one of the allowed routes.

        Uses multi-strategy matching:
        1. Direct exact match
        2. Case-insensitive match
        3. XML extraction (for providers that wrap in XML)
        4. Fallback to first route

        Args:
            raw_response: Raw LLM response
            allowed_routes: List of valid route names
            node_id: Node ID for logging

        Returns:
            Normalized route name
        """
        normalized, _ = RouteNormalizer.normalize_route_response(
            raw_response,
            allowed_routes,
            node_id
        )
        return normalized

    async def _process_so_billing(
        self,
        so_data: StructuredOutputNodeData,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        token_usage: Optional[Dict]
    ):
        """
        Process billing for the structured output execution.

        Args:
            so_data: Structured output node configuration
            workflow_run: Current workflow run
            node: The execution node
            token_usage: Token usage from LLM call
        """
        llm = await self._get_llm_for_so_node(so_data)
        user = await self._get_user_from_workflow_run(workflow_run)

        node_db_id = await database_sync_to_async(lambda: node.db_node.id)()

        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node_db_id
        )

    async def _get_llm_for_so_node(
        self, so_data: StructuredOutputNodeData
    ) -> LLM:
        """
        Get the LLM to use for this structured output node.

        Returns the LLM configured for this node, or falls back to
        the first available default provider model.

        Args:
            so_data: Structured output node configuration

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        llm = await database_sync_to_async(lambda: so_data.llm)()

        if llm:
            return llm

        # Fallback to default provider
        logger.warning(
            f"No LLM configured for structured output node, falling back to {LLMDefaults.DEFAULT_PROVIDER}"
        )

        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(
                provider=LLMDefaults.DEFAULT_PROVIDER
            ).first()
        )()

        if not default_llm:
            raise ValueError(
                f"No LLM configured for structured output node and no default {LLMDefaults.DEFAULT_PROVIDER} model found"
            )

        return default_llm

    async def _handle_human_validation_required(
        self,
        workflow_run_step: WorkflowRunStep,
        selected_route: str,
        explanation: Optional[str],
        routes: List[Dict],
        route_names: List[str],
        raw_response: str,
        node: ExecutionNode,
        so_data: StructuredOutputNodeData,
        start_time,
        correlation_id: str
    ) -> NodeExecutionResult:
        """
        Handle case where human validation is required.

        Updates workflow step with pending status and metadata containing
        AI recommendation for user review.

        Args:
            workflow_run_step: Workflow run step to update
            selected_route: AI-selected route
            explanation: Explanation for route selection
            routes: Full route objects
            route_names: List of route names
            raw_response: Raw LLM response
            node: Execution node
            so_data: Structured output node configuration
            start_time: Execution start time
            correlation_id: Correlation ID for logging

        Returns:
            NodeExecutionResult with pending_human_input status
        """
        logger.info(
            f"[{correlation_id}] Human validation required. "
            f"AI recommendation: {selected_route}"
        )

        # Build metadata using standardized MetadataKey constants
        # NOTE: All keys MUST be snake_case - DRF converts to camelCase for frontend
        # Uses same structure as ConditionalNodeHandler for consistency
        metadata = {
            MetadataKey.AI_RECOMMENDATION: selected_route,
            MetadataKey.ANALYSIS: explanation,  # Unified key (was 'explanation')
            MetadataKey.AVAILABLE_ROUTES: routes,  # Full route objects (was route_names)
            MetadataKey.PENDING_HUMAN_VALIDATION: True,
            MetadataKey.RAW_RESPONSE: raw_response if raw_response != selected_route else None,
        }

        await self._update_step_status(
            workflow_run_step,
            WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
            response=f"AI recommends: {selected_route}",
            metadata=metadata
        )

        end_time = timezone.now()
        execution_time = (end_time - start_time).total_seconds()

        return NodeExecutionResult(
            success=False,
            output=selected_route,
            error=ErrorCode.PENDING_HUMAN_INPUT,  # Critical: This signals workflow to pause
            token_usage=None,
            execution_time=execution_time,
            metadata=metadata
        )
