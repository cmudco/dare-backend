"""
Base routing handler for workflow routing nodes.

Shared functionality for routing nodes (StructuredOutputNode):
- LLM query with structured output spec
- Route parsing and validation
- Metadata building (unified format)
- Human validation flow
"""
import json
import logging
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.models import LLM
from core.services.dtos import LLMQueryRequestBuilder
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.handlers.event_emitter import EventEmitter
from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.utils import (
    ErrorCode,
    LLMDefaults,
    MetadataKey,
    RouteNormalizer,
    StructuredOutputBuilder,
)
from workflows.models import WorkflowRun, WorkflowRunStep
from workflows.models.nodes import BaseNodeData


logger = logging.getLogger(__name__)


class BaseRoutingHandler(BaseExecutionHandler):
    """
    Base handler for routing nodes (StructuredOutputNode).

    Subclasses must implement:
    - can_handle(node_type) -> bool
    - _get_input_for_evaluation(node, context) -> str
    - _get_prompt_for_routing(node_data, routes, route_names) -> str
    - _get_node_data(node) -> NodeData
    - _get_llm_for_node(node_data) -> LLM
    - _get_node_type_name() -> str
    """

    # ==================== Abstract Methods ====================

    @abstractmethod
    async def _get_input_for_evaluation(
        self, node: ExecutionNode, context: NodeExecutionContext,
    ) -> Optional[str]:
        pass

    @abstractmethod
    async def _get_prompt_for_routing(
        self, node_data: BaseNodeData, routes: List[Dict[str, str]], route_names: List[str],
    ) -> str:
        pass

    @abstractmethod
    async def _get_node_data(self, node: ExecutionNode) -> Optional[BaseNodeData]:
        pass

    @abstractmethod
    async def _get_llm_for_node(self, node_data: BaseNodeData) -> LLM:
        pass

    @abstractmethod
    def _get_node_type_name(self) -> str:
        pass

    # ==================== LLM Routing Query ====================

    async def _query_llm_for_routing(
        self,
        message: str,
        llm: LLM,
        routes: List[Dict],
        route_names: List[str],
        structured_spec: Optional[Dict],
        workflow_run: WorkflowRun,
        correlation_id: str,
        emitter: EventEmitter,
        node_id: Optional[str] = None,
        workflow_run_step: Optional[WorkflowRunStep] = None,
    ) -> Tuple[str, Optional[str], Optional[Dict]]:
        """
        Execute LLM query for routing decision with structured output.

        Returns (selected_route, analysis_text, token_usage).
        """
        logger.debug(f"[{correlation_id}] Evaluating routing with LLM: {llm.identifier}")

        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        request = LLMQueryRequestBuilder.from_workflow_data(
            message=message,
            user=user,
            llm=llm,
            max_tokens=LLMDefaults.STRUCTURED_OUTPUT_MAX_TOKENS,
            temperature=LLMDefaults.STRUCTURED_OUTPUT_TEMPERATURE,
            structured_spec=structured_spec,
        )

        full_response, token_usage = await self._execute_llm_query_with_collection(
            self.llm_service.query(request),
            emitter=emitter,
            node_id=node_id,
        )

        logger.debug(f"[{correlation_id}] LLM response received, parsing routing decision")

        selected_route, analysis_text = self._parse_routing_response(
            full_response, route_names, correlation_id,
        )

        return selected_route, analysis_text, token_usage

    # ==================== Response Parsing ====================

    def _parse_routing_response(
        self,
        response: str,
        route_names: List[str],
        correlation_id: str,
    ) -> Tuple[str, Optional[str]]:
        """Parse routing decision from JSON LLM response."""
        analysis_text = None
        selected_route = None

        try:
            data = json.loads(response)
            if isinstance(data, dict):
                route_value = data.get('route')
                if route_value:
                    for route_name in route_names:
                        if str(route_value).lower() == route_name.lower():
                            selected_route = route_name
                            break

                if selected_route:
                    analysis_text = data.get('explanation', '').strip() or None
                    logger.debug(
                        f"[{correlation_id}] Extracted route '{selected_route}' from JSON"
                    )
                    return selected_route, analysis_text

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[{correlation_id}] JSON parse failed: {e}, response: {response[:200]}")

        # Fallback: fuzzy matching on raw response
        normalized_route, _ = RouteNormalizer.normalize_route_response(
            response, route_names, correlation_id,
        )

        return normalized_route, analysis_text

    # ==================== Structured Output Spec ====================

    def _build_structured_output_spec(
        self,
        route_names: List[str],
        include_explanation: bool = True,
    ) -> Optional[Dict]:
        """Build unified structured output specification for routing."""
        return StructuredOutputBuilder.build_structured_spec(
            route_names,
            field_name="route",
            description="Route selection decision",
            include_explanation=include_explanation,
        )

    # ==================== Metadata ====================

    async def _build_routing_metadata(
        self,
        selected_route: str,
        analysis_text: Optional[str],
        routes: List[Dict[str, str]],
        is_human_validated: bool = False,
        raw_response: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build standardized metadata dictionary for routing nodes."""
        metadata = {
            MetadataKey.SELECTED_ROUTE: selected_route,
            MetadataKey.ANALYSIS: analysis_text,
            MetadataKey.AVAILABLE_ROUTES: routes,
            MetadataKey.IS_HUMAN_VALIDATED: is_human_validated,
        }

        if raw_response and raw_response != selected_route:
            metadata[MetadataKey.RAW_RESPONSE] = raw_response

        return metadata

    # ==================== Human Validation ====================

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
        raw_response: Optional[str] = None,
    ) -> NodeExecutionResult:
        """Handle case where human validation is required. Pauses execution."""
        logger.info(
            f"[{correlation_id}] Human validation required. AI recommendation: {selected_route}"
        )

        metadata = {
            MetadataKey.AI_RECOMMENDATION: selected_route,
            MetadataKey.ANALYSIS: analysis_text or "",
            MetadataKey.AVAILABLE_ROUTES: routes,
            MetadataKey.PENDING_HUMAN_VALIDATION: True,
            MetadataKey.SELECTED_ROUTE: selected_route,
        }

        if raw_response and raw_response != selected_route:
            metadata[MetadataKey.RAW_RESPONSE] = raw_response

        await self._update_step_status(
            workflow_run_step,
            WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
            response=f"AI recommends: {selected_route}",
            metadata=metadata,
        )

        execution_time = (timezone.now() - start_time).total_seconds()

        custom_prompt = await database_sync_to_async(
            lambda: node_data.prompt.content if node_data.prompt else ""
        )()

        return NodeExecutionResult(
            success=False,
            output=selected_route,
            error=ErrorCode.PENDING_HUMAN_INPUT,
            execution_time=execution_time,
            metadata={
                MetadataKey.PENDING_HUMAN_VALIDATION: True,
                MetadataKey.AI_RECOMMENDATION: selected_route,
                MetadataKey.ANALYSIS: analysis_text or "",
                MetadataKey.AVAILABLE_ROUTES: routes,
                'node_id': node.id,
                'label': node.label,
                'custom_prompt': custom_prompt,
            },
        )

    # ==================== Helpers ====================

    async def _get_default_llm(self) -> LLM:
        """Get the default fallback LLM."""
        logger.warning(
            f"No LLM configured for {self._get_node_type_name()}, "
            f"falling back to {LLMDefaults.DEFAULT_PROVIDER}"
        )

        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(provider=LLMDefaults.DEFAULT_PROVIDER).first()
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
        token_usage: Optional[Dict],
    ) -> None:
        """Process billing for the routing execution."""
        llm = await self._get_llm_for_node(node_data)
        user = await self._get_user_from_workflow_run(workflow_run)
        node_db_id = await database_sync_to_async(lambda: node.db_node.id)()

        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node_db_id,
        )

    async def _get_routes_from_node_data(self, node_data: BaseNodeData) -> List[Dict[str, str]]:
        """Get routes configuration from node data."""
        return await database_sync_to_async(lambda: node_data.get_routes())()
