"""
Conditional node handler for workflow execution.

This handler routes workflow execution based on AI evaluation or human validation.

Refactored to use utility modules following LLM provider patterns.
"""
import logging
from typing import Optional, Tuple, Dict, Any, List
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep, ConditionalNodeData
from workflows.constants import WorkflowRunStepStatus
from workflows.services.conditional_prompt_service import ConditionalPromptService
from conversations.models import LLM

# Import new utility modules
from workflows.handlers.utils import (
    NodeType,
    LLMDefaults,
    ErrorCode,
    MetadataKey,
    ErrorResultBuilder,
    NodeDataValidator,
    InputValidator,
    ConditionalMessagePreparer,
    RouteNormalizer,
    LLMConfig,
)


logger = logging.getLogger(__name__)


class ConditionalNodeHandler(BaseExecutionHandler):
    """
    Handler for 'conditional' type nodes.

    This handler orchestrates conditional routing by:
    1. Validating node configuration and extracting input
    2. Evaluating input using AI with configured LLM
    3. Parsing routing decisions with XML extraction
    4. Handling human validation workflows when required
    5. Processing billing and status updates via base handler

    Enhanced with utility modules for better code quality, maintainability,
    and consistency with LLM provider patterns.

    Human Validation Mode:
        When enabled, AI provides a recommendation but workflow execution pauses
        for human approval. The workflow service must call resume_workflow_after_human_validation
        to continue execution with the user's choice.
    """

    def can_handle(self, node_type: str) -> bool:
        """
        Check if this handler can process the given node type.

        Args:
            node_type: The type of node to check

        Returns:
            True if node_type is 'conditional', False otherwise
        """
        return node_type == NodeType.CONDITIONAL

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a conditional node by evaluating input and choosing a route.

        This method orchestrates the complete conditional execution workflow including
        input validation, AI evaluation, decision parsing, human validation handling,
        billing, and result processing.

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
            conditional_data = await self._get_and_validate_conditional_data(node)
            if conditional_data is None:
                return ErrorResultBuilder.build_validation_error_result(
                    node_id=node.id,
                    node_type=NodeType.CONDITIONAL,
                    validation_message="Invalid or missing conditional node data"
                )

            # Get or create workflow run step for conditional node
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

            # Extract and validate input using utility
            input_output = await self._extract_conditional_input(node, context)
            if not input_output:
                return ErrorResultBuilder.build_error_result(
                    Exception("No input provided to conditional node"),
                    context={'node_id': node.id, 'node_type': NodeType.CONDITIONAL}
                )

            # Get routes configuration
            routes = await database_sync_to_async(
                lambda: conditional_data.get_routes()
            )()

            if not routes or len(routes) == 0:
                return ErrorResultBuilder.build_error_result(
                    Exception("No routes defined for conditional node"),
                    context={'node_id': node.id, 'node_type': NodeType.CONDITIONAL}
                )

            # Evaluate routing decision using LLM
            routing_decision, analysis_text, token_usage = await self._evaluate_routing_decision(
                conditional_data,
                routes,
                input_output,
                context.workflow_run,
                correlation_id
            )

            # Process billing using base handler
            await self._process_conditional_billing(
                conditional_data, context.workflow_run, node, token_usage
            )

            # Check if human validation is required
            require_human_validation = await database_sync_to_async(
                lambda: conditional_data.require_human_validation
            )()

            if require_human_validation:
                return await self._handle_human_validation_required(
                    workflow_run_step=workflow_run_step,
                    routing_decision=routing_decision,
                    analysis_text=analysis_text,
                    routes=routes,
                    input_output=input_output,
                    node=node,
                    conditional_data=conditional_data,
                    start_time=start_time,
                    correlation_id=correlation_id
                )

            # No human validation required - proceed with AI decision
            metadata = {
                MetadataKey.ROUTING_DECISION: routing_decision,
                'analysis': analysis_text,
                MetadataKey.AVAILABLE_ROUTES: [r['name'] for r in routes],
                MetadataKey.IS_HUMAN_VALIDATED: False
            }

            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.COMPLETED,
                response=routing_decision,
                metadata=metadata
            )

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Successfully executed conditional node in {execution_time:.2f}s. "
                f"Routing: {routing_decision}"
            )

            return NodeExecutionResult(
                success=True,
                output=routing_decision,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata={
                    MetadataKey.ROUTING_DECISION: routing_decision,
                    MetadataKey.AVAILABLE_ROUTES: [r['name'] for r in routes],
                    'evaluated_input_length': len(input_output),
                    'analysis': analysis_text,
                    MetadataKey.IS_HUMAN_VALIDATED: False
                }
            )

        except Exception as e:
            # Use utility for error handling
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

    # ==================== Private Helper Methods ====================

    async def _get_and_validate_conditional_data(
        self, node: ExecutionNode
    ) -> Optional[ConditionalNodeData]:
        """
        Get and validate conditional node data.

        Args:
            node: The execution node

        Returns:
            ConditionalNodeData if valid, None otherwise
        """
        conditional_data = await database_sync_to_async(
            lambda: node.db_node.data_object
        )()

        if not NodeDataValidator.validate_node_data_type(
            conditional_data, ConditionalNodeData, node.id
        ):
            return None

        return conditional_data

    async def _extract_conditional_input(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Extract input from dependencies for conditional evaluation.

        Conditional nodes require exactly one input source to avoid ambiguity.
        Uses ConditionalMessagePreparer utility for extraction.

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
            # Try fallback to current_input for backward compatibility
            if context.current_input:
                logger.debug(f"Using fallback current_input for conditional node {node.id}")
                return context.current_input

            logger.warning(f"Failed to extract input for conditional node {node.id}: {error}")
            return None

        return input_text

    async def _evaluate_routing_decision(
        self,
        conditional_data: ConditionalNodeData,
        routes: List[Dict],
        input_text: str,
        workflow_run: WorkflowRun,
        correlation_id: str
    ) -> Tuple[str, Optional[str], Optional[Dict]]:
        """
        Evaluate routing decision using LLM.

        Args:
            conditional_data: Conditional node configuration
            routes: List of available routes
            input_text: Input to evaluate
            workflow_run: Current workflow run
            correlation_id: Correlation ID for logging

        Returns:
            Tuple of (routing_decision, analysis_text, token_usage)
        """
        # Get LLM configuration
        llm = await self._get_llm_for_conditional(conditional_data)
        llm_provider = await database_sync_to_async(lambda: llm.provider)()

        # Build evaluation prompt using service
        evaluation_prompt = await database_sync_to_async(
            lambda: conditional_data.custom_prompt
        )()
        evaluation_prompt = evaluation_prompt or "Evaluate the input and choose the appropriate route."

        message = ConditionalPromptService.get_prompt_for_provider(
            provider=llm_provider,
            evaluation_prompt=evaluation_prompt,
            routes=routes,
            input_text=input_text
        )

        logger.debug(f"[{correlation_id}] Evaluating routing with LLM: {llm.identifier}")

        # Get user for LLM query
        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        # Execute LLM query with conditional configuration
        response_generator = self.llm_service.query(
            message=message,
            conversation=None,
            llm=llm,
            file_ids=None,
            embedding_ids=None,
            user_id=user.id,
            prompt_id=None,
            message_obj=None,
            workflow_run_step_obj=None,
            max_tokens=LLMDefaults.CONDITIONAL_MAX_TOKENS,
            temperature=LLMDefaults.CONDITIONAL_TEMPERATURE
        )

        # Use base handler to collect response
        full_response, token_usage = await self._execute_llm_query_with_collection(
            response_generator
        )

        logger.debug(f"[{correlation_id}] LLM response received, parsing routing decision")

        # Parse response using utility
        route_names = [r['name'] for r in routes]
        routing_decision, analysis_text = RouteNormalizer.extract_route_from_xml(
            full_response, route_names, f"conditional-{conditional_data.id}"
        )

        # Validate and fallback if needed
        if not routing_decision:
            logger.warning(f"[{correlation_id}] Failed to extract routing decision, using default")
            routing_decision = route_names[0]

        return routing_decision, analysis_text, token_usage

    async def _process_conditional_billing(
        self,
        conditional_data: ConditionalNodeData,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        token_usage: Optional[Dict]
    ):
        """
        Process billing for the conditional execution.

        Args:
            conditional_data: Conditional node configuration
            workflow_run: Current workflow run
            node: The execution node (to get db_node.id for billing)
            token_usage: Token usage from LLM call
        """
        llm = await self._get_llm_for_conditional(conditional_data)
        user = await self._get_user_from_workflow_run(workflow_run)
        
        node_db_id = await database_sync_to_async(lambda: node.db_node.id)()

        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node_db_id
        )

    async def _get_llm_for_conditional(
        self, conditional_data: ConditionalNodeData
    ) -> LLM:
        """
        Get the LLM to use for this conditional node.

        Returns the LLM configured for this conditional, or falls back to
        the first available default provider model.

        Args:
            conditional_data: Conditional node configuration

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        llm = await database_sync_to_async(lambda: conditional_data.llm)()

        if llm:
            return llm

        # Fallback to default provider
        logger.warning(
            f"No LLM configured for conditional, falling back to {LLMDefaults.DEFAULT_PROVIDER}"
        )

        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(
                provider=LLMDefaults.DEFAULT_PROVIDER
            ).first()
        )()

        if not default_llm:
            raise ValueError(
                f"No LLM configured and no {LLMDefaults.DEFAULT_PROVIDER} LLM available"
            )

        return default_llm

    async def _handle_human_validation_required(
        self,
        workflow_run_step: WorkflowRunStep,
        routing_decision: str,
        analysis_text: Optional[str],
        routes: List[Dict],
        input_output: str,
        node: ExecutionNode,
        conditional_data: ConditionalNodeData,
        start_time,
        correlation_id: str
    ) -> NodeExecutionResult:
        """
        Handle case where human validation is required.

        Updates workflow step status to PENDING_HUMAN_INPUT and returns
        a special error result that pauses workflow execution.

        Args:
            workflow_run_step: WorkflowRunStep to update
            routing_decision: AI recommended route
            analysis_text: AI analysis (optional)
            routes: Available routes
            input_output: Evaluated input
            node: Execution node
            conditional_data: Conditional node data
            start_time: Execution start time
            correlation_id: Correlation ID for logging

        Returns:
            NodeExecutionResult with pending human input status
        """
        # Build metadata using utility constants
        metadata = {
            MetadataKey.AI_RECOMMENDATION: routing_decision,
            'analysis': analysis_text or "",
            MetadataKey.AVAILABLE_ROUTES: [r['name'] for r in routes],
            MetadataKey.IS_HUMAN_VALIDATED: True,
            MetadataKey.PENDING_HUMAN_VALIDATION: True
        }

        await self._update_step_status(
            workflow_run_step,
            WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
            response=f"AI recommends: {routing_decision}",
            metadata=metadata
        )

        end_time = timezone.now()
        execution_time = (end_time - start_time).total_seconds()

        logger.info(
            f"[{correlation_id}] Conditional node requires human validation. "
            f"AI recommends: {routing_decision}"
        )

        # Get additional data for frontend
        step_number = await database_sync_to_async(
            lambda: conditional_data.step_number
        )()
        custom_prompt = await database_sync_to_async(
            lambda: conditional_data.custom_prompt
        )()

        # Return special result that pauses execution
        return NodeExecutionResult(
            success=False,
            error=ErrorCode.PENDING_HUMAN_INPUT,
            execution_time=execution_time,
            metadata={
                MetadataKey.PENDING_HUMAN_VALIDATION: True,
                MetadataKey.AI_RECOMMENDATION: routing_decision,
                MetadataKey.AI_ANALYSIS: analysis_text or "",
                MetadataKey.AVAILABLE_ROUTES: routes,
                'evaluated_input': input_output,
                'evaluated_input_length': len(input_output),
                'node_id': node.id,
                'step_number': step_number,
                'custom_prompt': custom_prompt
            }
        )
