"""
Base routing handler for workflow routing nodes.

This module provides a unified base handler for routing nodes (StructuredOutputNode)
that share common functionality:
- LLM query with structured output spec
- Route parsing and validation
- Metadata building (unified format)
- Human validation flow

StructuredOutputNodeHandler extends this base.
"""
import json
import logging
from abc import abstractmethod
from typing import Optional, Tuple, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowRun, WorkflowRunStep
from workflows.models.nodes import BaseNodeData
from workflows.constants import WorkflowRunStepStatus
from conversations.models import LLM
from core.services.dtos import LLMQueryRequestBuilder

from workflows.handlers.utils import (
    LLMDefaults,
    ErrorCode,
    MetadataKey,
    RouteNormalizer,
    StructuredOutputBuilder,
)


logger = logging.getLogger(__name__)


class BaseRoutingHandler(BaseExecutionHandler):
    """
    Base handler for routing nodes (StructuredOutputNode).

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
        node_data: BaseNodeData,
        routes: List[Dict[str, str]],
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
    async def _get_node_data(self, node: ExecutionNode) -> Optional[BaseNodeData]:
        """
        Get and validate the node-specific data object.

        Args:
            node: The execution node

        Returns:
            Node data object if valid, None otherwise
        """
        pass

    @abstractmethod
    async def _get_llm_for_node(self, node_data: BaseNodeData) -> LLM:
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
        correlation_id: str,
        send_callback=None,
        node_id: Optional[str] = None,
        workflow_run_step: Optional[WorkflowRunStep] = None,
    ) -> Tuple[str, Optional[str], Optional[Dict]]:
        """
        Execute LLM query for routing decision with structured output.

        All modern providers (OpenAI, Gemini, Claude) support native structured
        output and return JSON responses.

        Args:
            message: The prepared routing prompt
            llm: LLM to use for evaluation
            routes: List of route definitions
            route_names: List of route names for validation
            structured_spec: Structured output specification
            workflow_run: Current workflow run
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (selected_route, analysis_text, token_usage)
        """
        logger.debug(f"[{correlation_id}] Evaluating routing with LLM: {llm.identifier}")

        # Get user for LLM query
        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        # Build request with structured spec - all providers support native structured output
        request = LLMQueryRequestBuilder.from_workflow_data(
            message=message,
            user=user,
            llm=llm,
            max_tokens=LLMDefaults.STRUCTURED_OUTPUT_MAX_TOKENS,
            temperature=LLMDefaults.STRUCTURED_OUTPUT_TEMPERATURE,
            structured_spec=structured_spec
        )

        response_generator = self.llm_service.query(request)

        # Collect response using base handler
        full_response, token_usage = await self._execute_llm_query_with_collection(
            response_generator,
            send_callback=send_callback,
            node_id=node_id,
            workflow_run_id=workflow_run.id,
            workflow_run_step=workflow_run_step,
        )

        logger.debug(f"[{correlation_id}] LLM response received, parsing routing decision")

        # Extract route and analysis from JSON response
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

        All providers return JSON with structured outputs containing:
        - route: The selected route name
        - explanation: AI's reasoning for the choice

        Args:
            response: JSON response from LLM
            route_names: List of valid route names
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (selected_route, analysis_text)
        """
        analysis_text = None
        selected_route = None

        # Parse JSON response - all providers return JSON with native structured outputs
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                # Extract route - primary field name is 'route'
                route_value = data.get('route')
                if route_value:
                    # Match case-insensitively
                    for route_name in route_names:
                        if str(route_value).lower() == route_name.lower():
                            selected_route = route_name
                            break

                if selected_route:
                    # Extract explanation - primary field name is 'explanation'
                    analysis_text = data.get('explanation', '').strip() or None

                    logger.debug(
                        f"[{correlation_id}] Extracted route '{selected_route}' from JSON, "
                        f"has_explanation: {bool(analysis_text)}"
                    )
                    return selected_route, analysis_text

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{correlation_id}] JSON parse failed: {e}, response: {response[:200]}")

        # Fallback: fuzzy matching on raw response (for edge cases)
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
        routes: List[Dict[str, str]],
        is_human_validated: bool = False,
        raw_response: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build standardized metadata dictionary for routing nodes.

        This ensures StructuredOutputNode uses
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
        routes: List[Dict[str, str]],
        node: ExecutionNode,
        node_data: BaseNodeData,
        start_time,
        correlation_id: str,
        raw_response: Optional[str] = None
    ) -> NodeExecutionResult:
        """
        Handle case where human validation is required.

        Updates workflow step with pending status and metadata containing
        AI recommendation for user review. This is shared logic for both
        StructuredOutputNode.

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

        # Get custom prompt from node data
        custom_prompt = await database_sync_to_async(
            lambda: node_data.prompt.content if node_data.prompt else ""
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
                MetadataKey.ANALYSIS: analysis_text or "",
                MetadataKey.AVAILABLE_ROUTES: routes,
                'node_id': node.id,
                'label': node.label,
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
        node_data: BaseNodeData,
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

    async def _get_routes_from_node_data(self, node_data: BaseNodeData) -> List[Dict[str, str]]:
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
