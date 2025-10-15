"""
Conditional node handler for workflow execution.

This handler routes workflow execution based on AI evaluation or human validation.
"""
import logging
import xml.etree.ElementTree as ET
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep, ConditionalNodeData
from workflows.constants import WorkflowRunStepStatus
from workflows.node_handler_constants import DefaultValues
from workflows.services.conditional_prompt_service import ConditionalPromptService
from conversations.models import LLM


logger = logging.getLogger(__name__)


class ConditionalNodeHandler(BaseExecutionHandler):
    """
    Handler for 'conditional' type nodes.

    This handler evaluates input using AI and routes workflow execution
    based on the routing decision. Supports human validation mode where
    AI provides a recommendation but execution pauses for human approval.
    
    Uses BaseExecutionHandler for:
    - Billing processing
    - Status updates
    - Error handling
    - Token usage collection
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'conditional' nodes."""
        return node_type == 'conditional'

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a conditional node by evaluating input and choosing a route.

        This handler:
        1. Gets input from previous node
        2. Evaluates input using configured LLM
        3. Parses response to extract routing decision
        4. Either returns decision or pauses for human validation

        Args:
            node: The conditional node to execute
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with routing decision or pending human input
        """
        start_time = timezone.now()

        try:
            # Get conditional data from database
            conditional_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not conditional_data or not isinstance(conditional_data, ConditionalNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid conditional node data"
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

            # Update status to running using base handler
            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.RUNNING
            )

            # Get input from direct dependencies only
            input_output = await self._get_input_from_dependencies(node, context)

            if not input_output:
                return NodeExecutionResult(
                    success=False,
                    error="No input provided to conditional node"
                )

            # Get routes configuration
            routes = await database_sync_to_async(
                lambda: conditional_data.get_routes()
            )()

            if not routes or len(routes) == 0:
                return NodeExecutionResult(
                    success=False,
                    error="No routes defined for conditional node"
                )

            # Evaluate routing decision using LLM
            routing_decision, analysis_text, token_usage = await self._evaluate_routing(
                conditional_data,
                routes,
                input_output,
                context.workflow_run
            )

            # Process billing using base handler
            llm = await database_sync_to_async(lambda: conditional_data.llm)()
            if not llm:
                llm = await database_sync_to_async(
                    lambda: LLM.objects.filter(provider=DefaultValues.DEFAULT_LLM_PROVIDER).first()
                )()
            
            user = await self._get_user_from_workflow_run(context.workflow_run)
            await self._process_billing(
                token_usage=token_usage,
                llm=llm,
                user=user,
                step_node_id=None  # Conditional nodes don't have step_node_id
            )

            # Check if human validation is required
            require_human_validation = await database_sync_to_async(
                lambda: conditional_data.require_human_validation
            )()

            if require_human_validation:
                return await self._handle_human_validation_required(
                    workflow_run_step,
                    routing_decision,
                    analysis_text,
                    routes,
                    input_output,
                    node,
                    conditional_data,
                    start_time
                )

            # No human validation required - proceed with AI decision
            # Update step with metadata using base handler
            metadata = {
                'routing_decision': routing_decision,
                'analysis': analysis_text,
                'available_routes': [r['name'] for r in routes],
                'is_human_validated': False
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
                f"Successfully executed conditional node {node.id} in {execution_time:.2f}s. "
                f"Routing: {routing_decision}"
            )

            return NodeExecutionResult(
                success=True,
                output=routing_decision,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata={
                    'routing_decision': routing_decision,
                    'available_routes': [r['name'] for r in routes],
                    'evaluated_input_length': len(input_output),
                    'analysis': analysis_text,
                    'is_human_validated': False
                }
            )

        except Exception as e:
            # Use base handler error building
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
                logger.error(f"Failed to update conditional step status: {str(update_error)}")
            
            return result

    async def _get_input_from_dependencies(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> str:
        """
        Get input from direct node dependencies.

        Conditional nodes can only accept input from a single source to avoid
        ambiguity in routing decisions.

        Args:
            node: The conditional node
            context: Execution context with previous results

        Returns:
            Input string from the single direct dependency
        """
        # Get the workflow and edges to find direct input dependencies
        workflow = await database_sync_to_async(
            lambda: context.workflow_run.workflow
        )()
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Find nodes that directly connect TO this conditional node
        direct_inputs = []
        for edge in edges:
            if edge.target == node.id:
                direct_inputs.append(edge.source)

        # Validate that we have input from the single direct source
        input_output = None
        if context.previous_results and direct_inputs:
            valid_outputs = []

            for input_node_id in direct_inputs:
                if input_node_id in context.previous_results:
                    result_data = context.previous_results[input_node_id]

                    if result_data and isinstance(result_data, dict) and result_data.get('output'):
                        metadata = result_data.get('metadata') or {}
                        is_skipped = metadata.get('skipped', False)

                        if not is_skipped:
                            valid_outputs.append(result_data['output'])

            if len(valid_outputs) == 1:
                input_output = valid_outputs[0]
            elif len(valid_outputs) > 1:
                raise ValueError("Conditional nodes can only accept input from a single source")

        if not input_output and context.current_input:
            input_output = context.current_input

        return input_output

    async def _evaluate_routing(
        self,
        conditional_data: ConditionalNodeData,
        routes: list,
        input_text: str,
        workflow_run: WorkflowRun
    ) -> tuple[str, str, dict]:
        """
        Evaluate routing decision using LLM.

        Args:
            conditional_data: Conditional node configuration
            routes: List of available routes
            input_text: Input to evaluate
            workflow_run: Current workflow run

        Returns:
            tuple: (routing_decision, analysis_text, token_usage)
        """
        # Get LLM configuration
        llm = await database_sync_to_async(lambda: conditional_data.llm)()

        if not llm:
            # Fallback to first available LLM
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider=DefaultValues.DEFAULT_LLM_PROVIDER).first()
            )()

        llm_provider = await database_sync_to_async(lambda: llm.provider)()

        # Build evaluation prompt
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

        # Get user for LLM query
        workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        # Execute LLM query
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
            max_tokens=100,
            temperature=0.1
        )

        # Use base handler to collect response
        full_response, token_usage = await self._execute_llm_query_with_collection(
            response_generator
        )

        # Parse XML response
        routing_decision, analysis_text = self._parse_xml_response(full_response, routes)

        return routing_decision, analysis_text, token_usage

    def _parse_xml_response(self, response: str, routes: list) -> tuple[str, str]:
        """
        Parse XML response from LLM to extract routing decision and analysis.

        Args:
            response: XML response from LLM
            routes: Available routes

        Returns:
            tuple: (routing_decision, analysis_text)
        """
        routing_decision = None
        analysis_text = None

        try:
            xml_response = f"<root>{response.strip()}</root>"
            root = ET.fromstring(xml_response)

            decision_elem = root.find('.//decision')
            if decision_elem is not None and decision_elem.text:
                routing_decision = decision_elem.text.strip()

            analysis_elem = root.find('.//analysis')
            if analysis_elem is not None and analysis_elem.text:
                analysis_text = analysis_elem.text.strip()

        except ET.ParseError as parse_error:
            logger.warning(
                f"Failed to parse XML response: {parse_error}. Raw response: {response}"
            )

        # Validate routing decision
        route_names = [r['name'] for r in routes]

        if routing_decision not in route_names:
            logger.warning(
                f"Invalid or missing routing decision '{routing_decision}'. "
                f"Valid routes: {route_names}. Defaulting to {routes[0]['name']}."
            )
            routing_decision = routes[0]['name']

        return routing_decision, analysis_text

    async def _handle_human_validation_required(
        self,
        workflow_run_step: WorkflowRunStep,
        routing_decision: str,
        analysis_text: str,
        routes: list,
        input_output: str,
        node: ExecutionNode,
        conditional_data: ConditionalNodeData,
        start_time
    ) -> NodeExecutionResult:
        """
        Handle case where human validation is required.

        Args:
            workflow_run_step: WorkflowRunStep to update
            routing_decision: AI recommended route
            analysis_text: AI analysis
            routes: Available routes
            input_output: Evaluated input
            node: Execution node
            conditional_data: Conditional node data
            start_time: Execution start time

        Returns:
            NodeExecutionResult with pending human input status
        """
        # Use base handler to update step status with metadata
        metadata = {
            'ai_recommendation': routing_decision,
            'analysis': analysis_text,
            'available_routes': [r['name'] for r in routes],
            'is_human_validated': True,
            'pending_human_decision': True
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
            f"Conditional node {node.id} requires human validation. "
            f"AI recommends: {routing_decision}"
        )

        # Get step_number and custom_prompt
        step_number = await database_sync_to_async(lambda: conditional_data.step_number)()
        custom_prompt = await database_sync_to_async(lambda: conditional_data.custom_prompt)()

        # Return special result that pauses execution
        return NodeExecutionResult(
            success=False,
            error="PENDING_HUMAN_INPUT",
            execution_time=execution_time,
            metadata={
                'pending_human_validation': True,
                'ai_recommendation': routing_decision,
                'analysis': analysis_text,
                'available_routes': routes,
                'evaluated_input': input_output,
                'evaluated_input_length': len(input_output),
                'node_id': node.id,
                'step_number': step_number,
                'custom_prompt': custom_prompt
            }
        )

    async def _update_step_completed(
        self,
        workflow_run_step: WorkflowRunStep,
        routing_decision: str,
        analysis_text: str,
        routes: list
    ):
        """
        Update workflow run step as completed.

        NOTE: This method is deprecated - use base handler's _update_step_status instead.
        Kept for backward compatibility during transition.
        
        Args:
            workflow_run_step: WorkflowRunStep to update
            routing_decision: Final routing decision
            analysis_text: AI analysis
            routes: Available routes
        """
        # Use base handler method instead
        metadata = {
            'routing_decision': routing_decision,
            'analysis': analysis_text,
            'available_routes': [r['name'] for r in routes],
            'is_human_validated': False
        }
        
        await self._update_step_status(
            workflow_run_step,
            WorkflowRunStepStatus.COMPLETED,
            response=routing_decision,
            metadata=metadata
        )
