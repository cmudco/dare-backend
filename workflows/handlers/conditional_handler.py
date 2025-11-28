"""
Conditional node handler for workflow execution.

This handler routes workflow execution based on AI evaluation or human validation.
Extends BaseRoutingHandler for shared routing functionality.
"""
import logging
from typing import Optional, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.base_routing_handler import BaseRoutingHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowRun, ConditionalNodeData
from workflows.constants import WorkflowRunStepStatus
from workflows.services.conditional_prompt_service import ConditionalPromptService
from conversations.models import LLM

from workflows.handlers.utils import (
    NodeType,
    MetadataKey,
    ErrorResultBuilder,
    NodeDataValidator,
    ConditionalMessagePreparer,
)


logger = logging.getLogger(__name__)


class ConditionalNodeHandler(BaseRoutingHandler):
    """
    Handler for 'conditional' type nodes.

    This handler orchestrates conditional routing by:
    1. Extracting input from connected chatOutput node (conversation context)
    2. Using ConditionalPromptService for provider-specific prompt generation
    3. Evaluating input using AI with configured LLM
    4. Handling human validation workflows when required

    Conditional nodes require exactly one input from a chatOutput node,
    representing the conversation context to evaluate for routing.
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process the given node type."""
        return node_type == NodeType.CONDITIONAL

    def _get_node_type_name(self) -> str:
        """Get the human-readable name for this node type."""
        return "conditional"

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a conditional node by evaluating input and choosing a route.

        Args:
            node: The conditional node to execute
            context: Execution context with previous results and workflow info

        Returns:
            NodeExecutionResult with routing decision, or pending human input status
        """
        start_time = timezone.now()
        correlation_id = f"conditional-{node.id}"

        try:
            # Validate and get conditional configuration
            conditional_data = await self._get_node_data(node)
            if conditional_data is None:
                return ErrorResultBuilder.build_validation_error_result(
                    node_id=node.id,
                    node_type=NodeType.CONDITIONAL,
                    validation_message="Invalid or missing conditional node data"
                )

            # Get or create workflow run step
            step_number = await database_sync_to_async(
                lambda: conditional_data.step_number
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

            logger.info(f"[{correlation_id}] Starting conditional node execution")

            # Get input for evaluation (from chatOutput node)
            input_text = await self._get_input_for_evaluation(node, context)
            if not input_text:
                return ErrorResultBuilder.build_error_result(
                    Exception("No input provided to conditional node"),
                    context={'node_id': node.id, 'node_type': NodeType.CONDITIONAL}
                )

            # Get routes configuration
            routes = await self._get_routes_from_node_data(conditional_data)

            if not routes or len(routes) == 0:
                return ErrorResultBuilder.build_error_result(
                    Exception("No routes defined for conditional node"),
                    context={'node_id': node.id, 'node_type': NodeType.CONDITIONAL}
                )

            route_names = [r['name'] for r in routes]

            # Build routing prompt
            message = await self._get_prompt_for_routing(
                conditional_data, routes, route_names
            )
            # Append input to prompt (ConditionalPromptService expects input as parameter)
            # We use the service's format which includes input
            llm = await self._get_llm_for_node(conditional_data)
            llm_provider = await database_sync_to_async(lambda: llm.provider)()
            
            prompt_obj = await database_sync_to_async(
                lambda: conditional_data.prompt
            )()
            evaluation_prompt = await database_sync_to_async(
                lambda: prompt_obj.content if prompt_obj else "Evaluate the input and choose the appropriate route."
            )()

            message = ConditionalPromptService.get_prompt_for_provider(
                provider=llm_provider,
                evaluation_prompt=evaluation_prompt,
                routes=routes,
                input_text=input_text
            )

            # Build structured output spec
            structured_spec = self._build_structured_output_spec(route_names)

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
                conditional_data, context.workflow_run, node, token_usage
            )

            # Check if human validation is required
            require_human_validation = await database_sync_to_async(
                lambda: conditional_data.require_human_validation
            )()

            if require_human_validation:
                return await self._handle_human_validation_required(
                    workflow_run_step=workflow_run_step,
                    selected_route=selected_route,
                    analysis_text=analysis_text,
                    routes=routes,
                    node=node,
                    node_data=conditional_data,
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

            # Also add routing_decision key for backward compatibility
            metadata[MetadataKey.ROUTING_DECISION] = selected_route

            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.COMPLETED,
                response=selected_route,
                metadata=metadata
            )

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Successfully executed conditional node in {execution_time:.2f}s. "
                f"Routing: {selected_route}"
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
                f"[{correlation_id}] Conditional node execution failed: {str(e)}",
                exc_info=True
            )

            result = self._build_error_result(e, node, start_time)

            # Update workflow run step with error
            try:
                workflow_run_step = await self._get_or_create_workflow_run_step(
                    context.workflow_run,
                    node,
                    0
                )
                await self._update_step_status(
                    workflow_run_step,
                    WorkflowRunStepStatus.FAILED,
                    error=result.error
                )
            except Exception as update_error:
                logger.error(
                    f"[{correlation_id}] Failed to update conditional step status: {str(update_error)}"
                )

            return result

    # ==================== Abstract Method Implementations ====================

    async def _get_node_data(self, node: ExecutionNode) -> Optional[ConditionalNodeData]:
        """Get and validate conditional node data."""
        conditional_data = await database_sync_to_async(
            lambda: node.db_node.data_object
        )()

        if not NodeDataValidator.validate_node_data_type(
            conditional_data, ConditionalNodeData, node.id
        ):
            return None

        return conditional_data

    async def _get_input_for_evaluation(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Extract input from dependencies for conditional evaluation.

        Conditional nodes require exactly one input source from a chatOutput node
        to ensure unambiguous evaluation of conversation context.

        Args:
            node: The conditional node
            context: Execution context with previous results

        Returns:
            Input string if valid, None otherwise
        """
        # Get direct input dependencies from workflow graph
        workflow = await database_sync_to_async(
            lambda: context.workflow_run.workflow
        )()
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Find nodes that directly connect TO this conditional node
        direct_inputs = [edge.source for edge in edges if edge.target == node.id]

        # Filter previous results to only include direct dependencies
        direct_previous_results = {
            node_id: result
            for node_id, result in (context.previous_results or {}).items()
            if node_id in direct_inputs
        }

        # Use utility to extract single input
        success, error, input_text = ConditionalMessagePreparer.extract_single_input_from_results(
            direct_previous_results
        )

        if not success:
            logger.warning(f"Failed to extract input for conditional node {node.id}: {error}")
            return None

        return input_text

    async def _get_prompt_for_routing(
        self,
        node_data: ConditionalNodeData,
        routes: List[Dict],
        route_names: List[str]
    ) -> str:
        """
        Build the routing prompt for conditional node.

        Uses ConditionalPromptService for provider-specific prompt generation.
        The actual prompt construction is done in execute() after getting the LLM.

        Args:
            node_data: Conditional node configuration
            routes: List of route definitions
            route_names: List of route names

        Returns:
            Base evaluation prompt (input is added separately)
        """
        prompt_obj = await database_sync_to_async(
            lambda: node_data.prompt
        )()
        
        return await database_sync_to_async(
            lambda: prompt_obj.content if prompt_obj else "Evaluate the input and choose the appropriate route."
        )()

    async def _get_llm_for_node(self, node_data: ConditionalNodeData) -> LLM:
        """
        Get the LLM configured for this conditional node.

        Args:
            node_data: Conditional node configuration

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        llm = await database_sync_to_async(lambda: node_data.llm)()

        if llm:
            return llm

        return await self._get_default_llm()
