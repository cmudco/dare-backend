"""
Base routing handler for workflow routing nodes.

This module provides a unified base handler for routing nodes (ConditionalNode, 
StructuredOutputNode) that share common functionality:
- LLM query with structured output spec
- Route parsing and validation
- Metadata building (unified format)
- Human validation flow

Both ConditionalNodeHandler and StructuredOutputNodeHandler extend this base.
"""
import logging
from abc import abstractmethod
from typing import Optional, Tuple, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowRun, WorkflowRunStep
from workflows.constants import WorkflowRunStepStatus
from conversations.models import LLM
from core.services.llm_utils import SchemaTransformer
from core.services.dtos import LLMQueryRequestBuilder

from workflows.handlers.utils import (
    LLMDefaults,
    ErrorCode,
    MetadataKey,
    RouteNormalizer,
    StructuredOutputBuilder,
    RouteInstructionBuilder,
)


logger = logging.getLogger(__name__)


class BaseRoutingHandler(BaseExecutionHandler):
    """
    Base handler for routing nodes (ConditionalNode, StructuredOutputNode).

    Shared functionality:
    - LLM query with structured output spec
    - Route parsing and validation
    - Metadata building (unified format)
    - Human validation flow

    Subclasses must implement:
    - can_handle(node_type) -> bool
    - _get_input_for_evaluation(node, context) -> str
    - _get_prompt_for_routing(node_data, routes) -> str
    - _get_node_data(node) -> NodeData
    - _get_llm_for_node(node_data) -> LLM
    """

    # ==================== Abstract Methods ====================

    @abstractmethod
    async def _get_input_for_evaluation(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Get the input text to evaluate for routing decision.

        Args:
            node: The routing node
            context: Execution context with previous results

        Returns:
            Input string for routing evaluation, or None if not available
        """
        pass

    @abstractmethod
    async def _get_prompt_for_routing(
        self,
        node_data: Any,
        routes: List[Dict],
        route_names: List[str]
    ) -> str:
        """
        Build the routing prompt for this node type.

        Args:
            node_data: Node-specific configuration data
            routes: List of route definitions [{name, description}]
            route_names: List of route names

        Returns:
            Complete prompt string for LLM evaluation
        """
        pass

    @abstractmethod
    async def _get_node_data(self, node: ExecutionNode) -> Optional[Any]:
        """
        Get and validate the node-specific data object.

        Args:
            node: The execution node

        Returns:
            Node data object if valid, None otherwise
        """
        pass

    @abstractmethod
    async def _get_llm_for_node(self, node_data: Any) -> LLM:
        """
        Get the LLM configured for this node.

        Args:
            node_data: Node-specific configuration data

        Returns:
            LLM instance to use for routing

        Raises:
            ValueError: If no LLM can be determined
        """
        pass

    @abstractmethod
    def _get_node_type_name(self) -> str:
        """
        Get the human-readable name for this node type.

        Returns:
            Node type name for logging and error messages
        """
        pass

    # ==================== Shared Core Methods ====================

    async def _query_llm_for_routing(
        self,
        message: str,
        llm: LLM,
        routes: List[Dict],
        route_names: List[str],
        structured_spec: Optional[Dict],
        workflow_run: WorkflowRun,
        correlation_id: str
    ) -> Tuple[str, Optional[str], Optional[Dict]]:
        """
        Execute LLM query for routing decision with optional structured output.

        This is the shared LLM query logic used by both ConditionalNode and
        StructuredOutputNode.

        Args:
            message: The prepared routing prompt
            llm: LLM to use for evaluation
            routes: List of route definitions
            route_names: List of route names for validation
            structured_spec: Optional structured output specification
            workflow_run: Current workflow run
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (selected_route, analysis_text, token_usage)
        """
        logger.debug(f"[{correlation_id}] Evaluating routing with LLM: {llm.identifier}")

        # Get LLM provider info
        llm_provider = await database_sync_to_async(lambda: llm.provider)()
        llm_identifier = await database_sync_to_async(lambda: llm.identifier)()

        # Check if provider supports native structured output
        supports_native = SchemaTransformer.supports_native_structured_output(llm_provider)

        # If provider doesn't support native structured output, append XML instructions
        final_message = message
        if not supports_native and structured_spec:
            instruction = RouteInstructionBuilder.build_simple_instruction(route_names)
            final_message = f"{message}{instruction}"
            logger.debug(
                f"[{correlation_id}] Provider {llm_provider} doesn't support native structured output, "
                "added XML instructions to message"
            )

        # Get user for LLM query
        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        # Build request with structured spec (only pass if provider supports it)
        request = LLMQueryRequestBuilder.from_workflow_data(
            message=final_message,
            user=user,
            llm=llm,
            max_tokens=LLMDefaults.CONDITIONAL_MAX_TOKENS,
            temperature=LLMDefaults.CONDITIONAL_TEMPERATURE,
            structured_spec=structured_spec if supports_native else None
        )

        response_generator = self.llm_service.query(request)

        # Collect response using base handler
        full_response, token_usage = await self._execute_llm_query_with_collection(
            response_generator
        )

        logger.debug(f"[{correlation_id}] LLM response received, parsing routing decision")

        # Extract route and analysis from response
        selected_route, analysis_text = self._parse_routing_response(
            full_response,
            route_names,
            correlation_id
        )

        return selected_route, analysis_text, token_usage

    def _parse_routing_response(
        self,
        response: str,
        route_names: List[str],
        correlation_id: str
    ) -> Tuple[str, Optional[str]]:
        """
        Parse routing decision from LLM response.

        Supports multiple response formats:
        1. JSON with route + explanation fields
        2. XML with <decision>/<route> and <analysis> tags
        3. Plain text route name with multi-line explanation

        Args:
            response: Raw LLM response
            route_names: List of valid route names
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (selected_route, analysis_text)
        """
        analysis_text = None
        selected_route = None

        # Strategy 1: Try JSON extraction first (for structured outputs)
        try:
            import json
            data = json.loads(response)
            if isinstance(data, dict):
                # Extract route from JSON - try multiple field names
                for field in ['route', 'decision', 'choice', 'selection']:
                    if field in data:
                        route_value = data[field]
                        # Match case-insensitively
                        for route_name in route_names:
                            if str(route_value).lower() == route_name.lower():
                                selected_route = route_name
                                break
                        if selected_route:
                            break

                if selected_route:
                    # Extract explanation from multiple possible fields
                    for exp_field in ['explanation', 'analysis', 'reasoning', 'rationale']:
                        exp_value = data.get(exp_field)
                        if exp_value and str(exp_value).strip():
                            analysis_text = str(exp_value).strip()
                            break

                    logger.debug(f"[{correlation_id}] Extracted route '{selected_route}' from JSON, analysis: {bool(analysis_text)}")
                    return selected_route, analysis_text

        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Try XML extraction for Claude-style responses
        selected_route, analysis_text = RouteNormalizer.extract_route_from_xml(
            response,
            route_names,
            correlation_id
        )

        if selected_route:
            return selected_route, analysis_text

        # Strategy 3: Try multi-line extraction (first line = route, rest = explanation)
        lines = response.strip().split('\n')
        if lines:
            first_line = lines[0].strip()
            # Check if first line matches a route
            for route_name in route_names:
                if first_line.lower() == route_name.lower() or route_name.lower() in first_line.lower():
                    selected_route = route_name
                    # Join remaining lines as explanation
                    if len(lines) > 1:
                        analysis_text = '\n'.join(lines[1:]).strip()
                    break

        if selected_route:
            logger.debug(f"[{correlation_id}] Extracted route '{selected_route}' from multi-line, analysis: {bool(analysis_text)}")
            return selected_route, analysis_text

        # Strategy 4: Fallback to direct matching
        normalized_route, _ = RouteNormalizer.normalize_route_response(
            response,
            route_names,
            correlation_id
        )

        return normalized_route, analysis_text

    def _build_structured_output_spec(
        self,
        route_names: List[str],
        include_explanation: bool = True
    ) -> Optional[Dict]:
        """
        Build unified structured output specification for routing.

        Args:
            route_names: List of valid route names
            include_explanation: Whether to include explanation field

        Returns:
            Structured output spec dictionary
        """
        return StructuredOutputBuilder.build_structured_spec(
            route_names,
            field_name="route",
            description="Route selection decision",
            include_explanation=include_explanation
        )

    async def _build_routing_metadata(
        self,
        selected_route: str,
        analysis_text: Optional[str],
        routes: List[Dict],
        is_human_validated: bool = False,
        raw_response: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build standardized metadata dictionary for routing nodes.

        This ensures both ConditionalNode and StructuredOutputNode use
        identical metadata format for consistency.

        Args:
            selected_route: The normalized selected route
            analysis_text: AI analysis/explanation text
            routes: Full route objects [{name, description}]
            is_human_validated: Whether this was human validated
            raw_response: Optional raw LLM response (if different from route)

        Returns:
            Standardized metadata dictionary using MetadataKey constants
        """
        metadata = {
            MetadataKey.SELECTED_ROUTE: selected_route,
            MetadataKey.ANALYSIS: analysis_text,  # Unified key for AI reasoning
            MetadataKey.AVAILABLE_ROUTES: routes,  # Full route objects
            MetadataKey.IS_HUMAN_VALIDATED: is_human_validated,
        }

        # Only include raw response if it differs from selected route
        if raw_response and raw_response != selected_route:
            metadata[MetadataKey.RAW_RESPONSE] = raw_response

        return metadata

    async def _handle_human_validation_required(
        self,
        workflow_run_step: WorkflowRunStep,
        selected_route: str,
        analysis_text: Optional[str],
        routes: List[Dict],
        node: ExecutionNode,
        node_data: Any,
        start_time,
        correlation_id: str,
        raw_response: Optional[str] = None
    ) -> NodeExecutionResult:
        """
        Handle case where human validation is required.

        Updates workflow step with pending status and metadata containing
        AI recommendation for user review. This is shared logic for both
        ConditionalNode and StructuredOutputNode.

        Args:
            workflow_run_step: Workflow run step to update
            selected_route: AI-selected route (becomes recommendation)
            analysis_text: AI analysis/explanation
            routes: Full route objects [{name, description}]
            node: Execution node
            node_data: Node-specific configuration data
            start_time: Execution start time
            correlation_id: Correlation ID for logging
            raw_response: Optional raw LLM response

        Returns:
            NodeExecutionResult with pending_human_input status
        """
        logger.info(
            f"[{correlation_id}] Human validation required. "
            f"AI recommendation: {selected_route}"
        )

        # Build metadata using standardized MetadataKey constants
        # NOTE: All keys MUST be snake_case - DRF converts to camelCase for frontend
        metadata = {
            MetadataKey.AI_RECOMMENDATION: selected_route,
            MetadataKey.ANALYSIS: analysis_text or "",
            MetadataKey.AVAILABLE_ROUTES: routes,  # Full route objects
            MetadataKey.PENDING_HUMAN_VALIDATION: True,
            MetadataKey.SELECTED_ROUTE: selected_route,  # Initially set to AI recommendation
        }

        if raw_response and raw_response != selected_route:
            metadata[MetadataKey.RAW_RESPONSE] = raw_response

        await self._update_step_status(
            workflow_run_step,
            WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
            response=f"AI recommends: {selected_route}",
            metadata=metadata
        )

        end_time = timezone.now()
        execution_time = (end_time - start_time).total_seconds()

        # Get additional data for frontend
        step_number = await database_sync_to_async(
            lambda: node_data.step_number
        )()
        prompt_obj = await database_sync_to_async(
            lambda: node_data.prompt
        )()
        custom_prompt = await database_sync_to_async(
            lambda: prompt_obj.content if prompt_obj else ""
        )()

        # Return special result that pauses execution
        return NodeExecutionResult(
            success=False,
            output=selected_route,
            error=ErrorCode.PENDING_HUMAN_INPUT,
            token_usage=None,
            execution_time=execution_time,
            metadata={
                MetadataKey.PENDING_HUMAN_VALIDATION: True,
                MetadataKey.AI_RECOMMENDATION: selected_route,
                MetadataKey.AI_ANALYSIS: analysis_text or "",
                MetadataKey.AVAILABLE_ROUTES: routes,
                'node_id': node.id,
                'step_number': step_number,
                'custom_prompt': custom_prompt
            }
        )

    async def _get_default_llm(self) -> LLM:
        """
        Get the default fallback LLM.

        Returns:
            Default LLM instance

        Raises:
            ValueError: If no default LLM is available
        """
        logger.warning(
            f"No LLM configured for {self._get_node_type_name()}, "
            f"falling back to {LLMDefaults.DEFAULT_PROVIDER}"
        )

        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(
                provider=LLMDefaults.DEFAULT_PROVIDER
            ).first()
        )()

        if not default_llm:
            raise ValueError(
                f"No LLM configured for {self._get_node_type_name()} and no default "
                f"{LLMDefaults.DEFAULT_PROVIDER} model found"
            )

        return default_llm

    async def _process_routing_billing(
        self,
        node_data: Any,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        token_usage: Optional[Dict]
    ):
        """
        Process billing for the routing execution.

        Args:
            node_data: Node-specific configuration data
            workflow_run: Current workflow run
            node: The execution node
            token_usage: Token usage from LLM call
        """
        llm = await self._get_llm_for_node(node_data)
        user = await self._get_user_from_workflow_run(workflow_run)

        node_db_id = await database_sync_to_async(lambda: node.db_node.id)()

        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node_db_id
        )

    async def _get_routes_from_node_data(self, node_data: Any) -> List[Dict]:
        """
        Get routes configuration from node data.

        Args:
            node_data: Node-specific configuration data

        Returns:
            List of route definitions [{name, description}]
        """
        return await database_sync_to_async(
            lambda: node_data.get_routes()
        )()

