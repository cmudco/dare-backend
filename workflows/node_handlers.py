"""
Node type handlers for workflow execution.

This module provides specialized handlers for different types of workflow nodes,
including step nodes, conditional nodes, and output nodes. Each handler encapsulates
the specific logic and requirements for executing that node type.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, AsyncGenerator, Tuple
from dataclasses import dataclass

from django.utils import timezone
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async

from workflows.models import (
    WorkflowNode, WorkflowRun, WorkflowRunStep,
    StepNodeData, ChatOutputNodeData, StartNodeData, ConditionalNodeData
)
from workflows.constants import WorkflowRunStepStatus
from workflows.node_handler_constants import DefaultValues
# ExecutionNode is now defined locally
from core.services.llm_service import LLMService
from core.services.billing_service import BillingService
from conversations.models import LLM


logger = logging.getLogger(__name__)


def categorize_error(exception: Exception) -> tuple[str, str]:
    """
    Categorize exception into error type and category.

    Returns:
        tuple: (error_category, error_type_name)
    """
    error_type = type(exception).__name__

    if isinstance(exception, (ValidationError, ValueError)):
        error_category = "Validation error"
    elif isinstance(exception, (ConnectionError, TimeoutError)):
        error_category = "Service error"
    else:
        error_category = "Unexpected error"

    return error_category, error_type


@dataclass
class ExecutionNode:
    """Simplified node representation for execution planning."""
    id: str
    type: str  # 'start', 'step', 'chatOutput', 'conditional'
    step_number: Optional[int]  # For step and output nodes
    db_node: WorkflowNode
    next_node_id: Optional[str] = None
    output_node_id: Optional[str] = None  # For step nodes, their corresponding output


@dataclass
class NodeExecutionContext:
    """Context for node execution."""
    workflow_run: WorkflowRun
    previous_results: Dict[str, Any]  # Results from previous nodes
    current_input: Optional[str] = None  # Direct input to this node


@dataclass
class NodeExecutionResult:
    """Result of node execution."""
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    token_usage: Optional[Dict] = None
    execution_time: Optional[float] = None
    metadata: Optional[Dict] = None


class BaseNodeHandler(ABC):
    """Base class for all node handlers."""

    def __init__(self):
        self.llm_service = LLMService()

    @abstractmethod
    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """
        Execute the node with given context.

        Args:
            node: The execution node to process
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with execution outcome
        """
        pass

    @abstractmethod
    def can_handle(self, node_type: str) -> bool:
        """
        Check if this handler can process the given node type.

        Args:
            node_type: The type of node to check

        Returns:
            bool: True if this handler can process the node type
        """
        pass

    async def _get_workflow_run_step(self, workflow_run: WorkflowRun, node: ExecutionNode) -> Optional[WorkflowRunStep]:
        """Get the WorkflowRunStep for this node if it exists."""
        try:
            return await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run,
                    step_node=node.db_node
                ).first()
            )()
        except:
            return None


class StepNodeHandler(BaseNodeHandler):
    """Handler for 'step' type nodes - executes LLM calls."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == 'step'

    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute a step node by calling the LLM with configured parameters."""
        start_time = timezone.now()

        try:
            # Get step data
            step_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not step_data or not isinstance(step_data, StepNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid step node data"
                )

            # Get or create workflow run step
            workflow_run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)

            # Update status to running
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.RUNNING
                )
            )()

            # Prepare the message
            message = await self._prepare_message(step_data, context)

            # Get LLM configuration
            llm = await self._get_llm_for_step(step_data)

            # Get file configurations
            content_file_ids = await database_sync_to_async(
                lambda: list(step_data.content_files.values_list('id', flat=True))
            )()

            embedding_file_ids = await database_sync_to_async(
                lambda: list(step_data.embedding_files.values_list('id', flat=True))
            )()

            # Get user and prompt info
            workflow = await database_sync_to_async(lambda: context.workflow_run.workflow)()
            user = await database_sync_to_async(lambda: workflow.user)()
            prompt_id = await database_sync_to_async(lambda: step_data.prompt.id if step_data.prompt else None)()

            # Execute LLM query
            response_generator = self.llm_service.query(
                message=message,
                conversation=None,
                llm=llm,
                file_ids=content_file_ids if content_file_ids else None,
                embedding_ids=embedding_file_ids if embedding_file_ids else None,
                user_id=user.id,
                prompt_id=prompt_id,
                message_obj=None,
                workflow_run_step_obj=workflow_run_step,
                max_tokens=step_data.max_tokens,
                temperature=step_data.temperature,
                max_context_snippets=step_data.max_context_snippets,
                document_similarity_threshold=step_data.document_similarity_threshold
            )

            # Collect response
            full_response = ""
            token_usage = {}
            async for chunk, usage in response_generator:
                full_response += chunk
                if usage:
                    token_usage = usage

            # Process billing for this step
            if token_usage and token_usage.get('input_tokens') and token_usage.get('output_tokens'):
                try:
                    billing_service = BillingService()

                    user = await database_sync_to_async(lambda: context.workflow_run.user)()
                    billing_success = await database_sync_to_async(
                        billing_service.process_workflow_billing
                    )(
                        user=user,
                        llm=step_data.llm,
                        input_tokens=token_usage['input_tokens'],
                        output_tokens=token_usage['output_tokens'],
                        step_node_id=node.db_node.id
                    )

                    if not billing_success:
                        logger.warning(f"Billing failed for step {node.id}, but continuing execution")
                except Exception as billing_error:
                    logger.error(f"Billing error for step {node.id}: {str(billing_error)}")
                    # Continue execution even if billing fails

            # Update workflow run step with results
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.COMPLETED,
                    response=full_response
                )
            )()

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(f"Successfully executed step node {node.id} in {execution_time:.2f}s")

            return NodeExecutionResult(
                success=True,
                output=full_response,
                token_usage=token_usage,
                execution_time=execution_time
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(f"{error_category} in step {node.id} ({error_type}): {str(e)}", exc_info=True)

            # Update workflow run step with error
            try:
                workflow_run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)
                await database_sync_to_async(
                    lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                        status=WorkflowRunStepStatus.FAILED,
                        error=error_msg
                    )
                )()
            except Exception as update_error:
                logger.error(f"Failed to update step status: {str(update_error)}")

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            return NodeExecutionResult(
                success=False,
                error=error_msg,
                execution_time=execution_time
            )

    async def _prepare_message(self, step_data: StepNodeData, context: NodeExecutionContext) -> str:
        """
        Prepare the message for LLM based on step configuration and context.

        Combines the step's prompt content with any previous step results to create
        the final message that will be sent to the LLM. Handles cases where no
        prompt is configured or no previous context exists. For multi-input steps,
        combines all input results.

        Args:
            step_data: StepNodeData with prompt configuration
            context: NodeExecutionContext with previous step results

        Returns:
            str: Formatted message ready for LLM processing
        """
        # Get base prompt content using async wrapper for Django ORM access
        prompt_content = ""
        prompt = await database_sync_to_async(lambda: step_data.prompt)()
        if prompt:
            prompt_content = await database_sync_to_async(lambda: prompt.content)()

        # Check if we have multiple previous results (multi-input step)
        previous_outputs = []
        if context.previous_results:
            for node_id, result_data in context.previous_results.items():
                if result_data and isinstance(result_data, dict) and 'output' in result_data and result_data['output']:
                    metadata = result_data.get('metadata') or {}
                    if not metadata.get('skipped', False):
                        previous_outputs.append(f"Result from {node_id}:\n{result_data['output']}")

        # Build message based on available inputs
        if previous_outputs:
            if len(previous_outputs) == 1:
                # Single input - use traditional format
                combined_input = previous_outputs[0].replace(f"Result from {list(context.previous_results.keys())[0]}:\n", "")
                if prompt_content:
                    message = f"{prompt_content}\n\nPrevious step result:\n{combined_input}"
                else:
                    message = combined_input
            else:
                # Multiple inputs - combine all results
                combined_input = "\n\n".join(previous_outputs)
                if prompt_content:
                    message = f"{prompt_content}\n\nResults from previous steps:\n{combined_input}"
                else:
                    message = combined_input
        elif context.current_input:
            # Fallback to current_input for backward compatibility
            if prompt_content:
                message = f"{prompt_content}\n\nPrevious step result:\n{context.current_input}"
            else:
                message = context.current_input
        else:
            message = prompt_content or DefaultValues.DEFAULT_TASK_MESSAGE

        return message

    async def _get_llm_for_step(self, step_data: StepNodeData) -> LLM:
        """
        Get the LLM to use for this step.

        Returns the LLM configured for this step, or falls back to the first
        available OpenAI model if no LLM is specifically configured.

        Args:
            step_data: StepNodeData with LLM configuration

        Returns:
            LLM: The language model to use for execution
        """
        llm = await database_sync_to_async(lambda: step_data.llm)()
        if not llm:
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider=DefaultValues.DEFAULT_LLM_PROVIDER).first()
            )()
        return llm

    @database_sync_to_async
    def _get_or_create_workflow_run_step(self, workflow_run: WorkflowRun, node: ExecutionNode) -> WorkflowRunStep:
        """Get or create a WorkflowRunStep for the step node."""
        step, created = WorkflowRunStep.objects.get_or_create(
            workflow_run=workflow_run,
            step_node=node.db_node,
            defaults={
                'order': node.step_number or 0,
                'status': WorkflowRunStepStatus.PENDING
            }
        )
        return step




class OutputNodeHandler(BaseNodeHandler):
    """Handler for 'chatOutput' type nodes - stores and formats output."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == 'chatOutput'

    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute an output node by storing the result from its corresponding step."""
        try:
            # Get output data
            output_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not output_data or not isinstance(output_data, ChatOutputNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid output node data"
                )

            # Get the input from context (should be from corresponding step node)
            output_content = context.current_input or "No output from step"
            status = "completed" if context.current_input else "failed"
            error_message = "" if context.current_input else "No input received from step"

            # Update the output node data
            await database_sync_to_async(
                lambda: ChatOutputNodeData.objects.filter(id=output_data.id).update(
                    status=status,
                    response=output_content,
                    error=error_message
                )
            )()

            logger.info(f"Successfully updated output node {node.id}")

            return NodeExecutionResult(
                success=True,
                output=output_content,
                metadata={
                    'output_node_updated': True,
                    'status': status
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(f"{error_category} in output node {node.id} ({error_type}): {str(e)}", exc_info=True)

            return NodeExecutionResult(
                success=False,
                error=error_msg
            )


class StartNodeHandler(BaseNodeHandler):
    """Handler for 'start' type nodes - initializes workflow execution."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == 'start'

    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute a start node by initializing workflow context."""
        try:
            # Get start data
            start_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not start_data or not isinstance(start_data, StartNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid start node data"
                )

            logger.info(f"Workflow '{start_data.title}' started in {start_data.mode} mode")

            return NodeExecutionResult(
                success=True,
                output=f"Workflow '{start_data.title}' initialized",
                metadata={
                    'workflow_title': start_data.title,
                    'workflow_mode': start_data.mode,
                    'workflow_description': start_data.description
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(f"{error_category} in start node {node.id} ({error_type}): {str(e)}", exc_info=True)

            return NodeExecutionResult(
                success=False,
                error=error_msg
            )


class ConditionalNodeHandler(BaseNodeHandler):
    """Handler for 'conditional' type nodes - routes workflow based on AI evaluation."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == 'conditional'

    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute a conditional node by evaluating input with AI or human and choosing a route."""
        start_time = timezone.now()

        try:
            # Get conditional data
            conditional_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not conditional_data or not isinstance(conditional_data, ConditionalNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid conditional node data"
                )

            # Get or create workflow run step for conditional node
            workflow_run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node, conditional_data.step_number)

            # Update status to running
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.RUNNING
                )
            )()

            # Get the workflow and edges to find direct input dependencies
            workflow = await database_sync_to_async(lambda: context.workflow_run.workflow)()
            edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

            # Find nodes that directly connect TO this conditional node
            direct_inputs = []
            for edge in edges:
                if edge.target == node.id:
                    direct_inputs.append(edge.source)

            # Validate that we have input from the single direct source
            input_output = None
            if context.previous_results and direct_inputs:
                # Get input only from direct dependencies, not all previous results
                valid_outputs = []

                for input_node_id in direct_inputs:
                    if input_node_id in context.previous_results:
                        result_data = context.previous_results[input_node_id]

                        if result_data and isinstance(result_data, dict) and 'output' in result_data and result_data['output']:
                            metadata = result_data.get('metadata') or {}
                            is_skipped = metadata.get('skipped', False)

                            if not is_skipped:
                                valid_outputs.append(result_data['output'])

                if len(valid_outputs) == 1:
                    input_output = valid_outputs[0]
                elif len(valid_outputs) > 1:
                    return NodeExecutionResult(
                        success=False,
                        error="Conditional nodes can only accept input from a single source"
                    )

            if not input_output and context.current_input:
                input_output = context.current_input

            if not input_output:
                return NodeExecutionResult(
                    success=False,
                    error="No input provided to conditional node"
                )

            # Get routes (supports n routes with backward compatibility)
            routes = await database_sync_to_async(lambda: conditional_data.get_routes())()
            
            if not routes or len(routes) == 0:
                return NodeExecutionResult(
                    success=False,
                    error="No routes defined for conditional node"
                )

            # Check if human validation is required
            require_human_validation = await database_sync_to_async(lambda: conditional_data.require_human_validation)()
            
            if require_human_validation:
                return await self._handle_human_validation(
                    node, conditional_data, context, input_output, 
                    workflow_run_step, routes, start_time
                )

            # Prepare AI evaluation message with n routes support
            evaluation_prompt = await database_sync_to_async(lambda: conditional_data.custom_prompt)()
            evaluation_prompt = evaluation_prompt or "Evaluate the input and choose the appropriate route."

            # Build route options dynamically for n routes
            route_options = "\n".join([
                f"- {route['name']}: {route.get('description', route['name'])}"
                for route in routes
            ])
            
            route_names = [route['name'] for route in routes]
            route_names_str = '", "'.join(route_names)

            message = f"""{evaluation_prompt}

Based on the following input, choose EXACTLY ONE route by responding with ONLY the route name (no explanation, no other text):

Route Options:
{route_options}

Input to evaluate:
{input_output}

Response format: Reply with ONLY one of: "{route_names_str}" - nothing else."""

            # Get LLM for evaluation - prefer Claude for consistent evaluation
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider="claude").first()
            )()

            if not llm:
                llm = await database_sync_to_async(
                    lambda: LLM.objects.filter(provider=DefaultValues.DEFAULT_LLM_PROVIDER).first()
                )()

            # Get user for LLM service
            user = await database_sync_to_async(lambda: workflow.user)()

            # Execute LLM query for routing decision
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
                max_tokens=10,  # Only need single word response
                temperature=0.1  # Very low temperature for deterministic routing
            )

            # Collect response
            full_response = ""
            token_usage = {}

            try:
                async for chunk, usage in response_generator:
                    if chunk:
                        full_response += chunk
                    if usage:
                        token_usage = usage

            except Exception as stream_error:
                raise

            # Process billing for this conditional node
            if token_usage and token_usage.get('input_tokens') and token_usage.get('output_tokens'):
                try:
                    billing_service = BillingService()

                    billing_success = await database_sync_to_async(
                        billing_service.process_workflow_billing
                    )(
                        user=user,
                        llm=llm,
                        input_tokens=token_usage['input_tokens'],
                        output_tokens=token_usage['output_tokens'],
                        step_node_id=node.db_node.id
                    )

                    if not billing_success:
                        logger.warning(f"Billing failed for conditional node {node.id}, but continuing execution")
                except Exception as billing_error:
                    logger.error(f"Billing error for conditional node {node.id}: {str(billing_error)}")
                    # Continue execution even if billing fails

            # Extract routing decision - simple cleanup since we forced single-word response
            routing_decision = full_response.strip()

            # Validate decision matches one of the routes (case-insensitive)
            decision_lower = routing_decision.lower()
            matched_route = None
            
            for route in routes:
                route_name_lower = route['name'].lower()
                
                if decision_lower == route_name_lower or route_name_lower in decision_lower:
                    matched_route = route['name']
                    break
            
            if not matched_route:
                # Default to first route if response is unclear
                logger.warning(f"Unclear routing decision '{routing_decision}' for node {node.id}, defaulting to {routes[0]['name']}")
                matched_route = routes[0]['name']
            
            routing_decision = matched_route

            # Update workflow run step with results
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.COMPLETED,
                    response=routing_decision  # Store the routing decision, not the full response
                )
            )()

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(f"Successfully executed conditional node {node.id} in {execution_time:.2f}s. Routing: {routing_decision}")

            return NodeExecutionResult(
                success=True,
                output=routing_decision,  # Return just the routing decision
                token_usage=token_usage,
                execution_time=execution_time,
                metadata={
                    'routing_decision': routing_decision,
                    'available_routes': [r['name'] for r in routes],
                    'evaluated_input_length': len(input_output),
                    'full_response': full_response,  # Store full response in metadata for debugging
                    'is_human_validated': False
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(f"{error_category} in conditional node {node.id} ({error_type}): {str(e)}", exc_info=True)

            # Update workflow run step with error
            try:
                await database_sync_to_async(
                    lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                        status=WorkflowRunStepStatus.FAILED,
                        error=error_msg
                    )
                )()
            except Exception as update_error:
                logger.error(f"Failed to update conditional step status: {str(update_error)}")

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            return NodeExecutionResult(
                success=False,
                error=error_msg,
                execution_time=execution_time
            )

    async def _handle_human_validation(
        self, 
        node: ExecutionNode, 
        conditional_data, 
        context: NodeExecutionContext,
        input_output: str,
        workflow_run_step: WorkflowRunStep,
        routes: list,
        start_time
    ) -> NodeExecutionResult:
        """
        Handle human validation by pausing execution and waiting for user input.
        
        This will pause the workflow and wait for the user to make a routing decision
        via the API endpoint.
        """
        try:
            # Format route options for display
            route_options_text = ', '.join([r['name'] for r in routes])
            
            # Update step status to PENDING_HUMAN_INPUT
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
                    response=f"Waiting for user to choose route. Available options: {route_options_text}"
                )
            )()
            
            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()
            
            logger.info(f"Conditional node {node.id} requires human validation. Pausing workflow.")
            
            # Return special result that pauses execution
            # The workflow execution service will detect this and halt
            return NodeExecutionResult(
                success=False,  # Marks as incomplete to pause workflow
                error="PENDING_HUMAN_INPUT",  # Special error code
                execution_time=execution_time,
                metadata={
                    'pending_human_validation': True,
                    'available_routes': routes,
                    'evaluated_input': input_output,
                    'evaluated_input_length': len(input_output),
                    'node_id': node.id,
                    'step_number': conditional_data.step_number,
                    'custom_prompt': conditional_data.custom_prompt
                }
            )
        except Exception as e:
            logger.error(f"Error setting up human validation for node {node.id}: {str(e)}", exc_info=True)
            return NodeExecutionResult(
                success=False,
                error=f"Failed to set up human validation: {str(e)}"
            )

    @database_sync_to_async
    def _get_or_create_workflow_run_step(self, workflow_run: WorkflowRun, node: ExecutionNode, step_number: int) -> WorkflowRunStep:
        """Get or create a WorkflowRunStep for the conditional node."""
        step, created = WorkflowRunStep.objects.get_or_create(
            workflow_run=workflow_run,
            step_node=node.db_node,
            defaults={
                'order': step_number,
                'status': WorkflowRunStepStatus.PENDING
            }
        )
        return step


class NodeHandlerRegistry:
    """Registry for managing node type handlers."""

    def __init__(self):
        """Initialize the registry with default handlers."""
        self._handlers: List[BaseNodeHandler] = []
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register the default node handlers."""
        self.register_handler(StepNodeHandler())
        self.register_handler(ConditionalNodeHandler())
        self.register_handler(OutputNodeHandler())
        self.register_handler(StartNodeHandler())

    def register_handler(self, handler: BaseNodeHandler):
        """Register a new node handler."""
        self._handlers.append(handler)

    def get_handler(self, node_type: str) -> Optional[BaseNodeHandler]:
        """Get the appropriate handler for a node type."""
        for handler in self._handlers:
            if handler.can_handle(node_type):
                return handler
        return None

    async def execute_node(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute a node using the appropriate handler."""
        handler = self.get_handler(node.type)
        if not handler:
            return NodeExecutionResult(
                success=False,
                error=f"No handler found for node type: {node.type}"
            )

        return await handler.execute(node, context)


# Global registry instance
node_handler_registry = NodeHandlerRegistry()