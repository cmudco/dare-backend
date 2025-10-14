"""
Node type handlers for workflow execution.

This module provides specialized handlers for different types of workflow nodes,
including step nodes, conditional nodes, and output nodes. Each handler encapsulates
the specific logic and requirements for executing that node type.
"""
import logging
import re
import xml.etree.ElementTree as ET
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
from workflows.services.conditional_prompt_service import ConditionalPromptService
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

            # If this step uses a Structured Output node, fetch allowed routes and
            # add strict instruction to return only one of them
            use_structured = await database_sync_to_async(lambda: step_data.use_structured_output_node)()
            allowed_routes: list[str] = []
            if use_structured:
                def _resolve_structured_routes():
                    # Resolve allowed routes for a Structured Output node connected to this step
                    wf_run = context.workflow_run
                    wf = wf_run.workflow
                    # Find structuredOutput -> this step
                    for e in wf.edges.all():
                        if e.target == node.id:
                            src_node = wf.nodes.filter(node_id=e.source, node_type='structuredOutput').first()
                            if src_node and src_node.data_object:
                                try:
                                    routes = src_node.data_object.get_routes()
                                    return [str(r.get('name', '')).strip() for r in routes if r and r.get('name')]
                                except Exception:
                                    return []
                    return []

                try:
                    allowed_routes = await database_sync_to_async(_resolve_structured_routes)()
                except Exception as route_err:
                    logger.warning(f"Failed to resolve structured routes for step {node.id}: {route_err}")

            if use_structured and allowed_routes:
                default_choice = allowed_routes[0]
                instruction = (
                    "\n\nROUTE SELECTION INSTRUCTIONS:\n"
                    f"Choose exactly one of: {', '.join(allowed_routes)}.\n"
                    "Return only the exact value with no quotes, punctuation, or explanation.\n"
                    f"If you are unsure or lack context, choose '{default_choice}'."
                )
                message = f"{message}{instruction}"
            try:
                logger.debug(
                    "Step %s message preview (first 300 chars): %s",
                    node.id,
                    (message or "")[:300],
                )
            except Exception:
                pass

            try:
                logger.debug(
                    "Step %s: use_structured_output_node=%s, text_input_len=%s, content_files=%s, embedding_files=%s",
                    node.id,
                    getattr(step_data, 'use_structured_output_node', False),
                    len(getattr(step_data, 'text_input', '') or ''),
                    await database_sync_to_async(lambda: step_data.content_files.count())(),
                    await database_sync_to_async(lambda: step_data.embedding_files.count())(),
                )
            except Exception:
                # Don't break execution if debug logging fails
                pass

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

            raw_response = (full_response or "")

            # If structured output is enabled, normalize the response to one allowed route
            selected_route = None
            if use_structured and allowed_routes:
                s = raw_response.strip().strip('"').strip("'")
                s = s.splitlines()[0].strip() if s else s
                # direct match
                if s in allowed_routes:
                    selected_route = s
                else:
                    # case-insensitive match
                    lower_map = {r.lower(): r for r in allowed_routes}
                    if s.lower() in lower_map:
                        selected_route = lower_map[s.lower()]
                    else:
                        # try first token
                        token = re.split(r"[^A-Za-z0-9_\-\.]+", s)[0] if s else ""
                        if token in allowed_routes:
                            selected_route = token
                        elif token.lower() in lower_map:
                            selected_route = lower_map[token.lower()]
                        else:
                            # default to first route to keep flow moving
                            selected_route = allowed_routes[0]
                            logger.warning(
                                "Step %s returned non-matching structured output '%s'; defaulting to '%s'",
                                node.id,
                                s,
                                selected_route,
                            )

                # Overwrite full_response with the selected route for routing
                full_response = selected_route

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
            def _update_step_completed():
                route_value = (full_response or "").strip()
                update_kwargs = {
                    'status': WorkflowRunStepStatus.COMPLETED,
                    'response': full_response,
                }
                try:
                    if getattr(step_data, 'use_structured_output_node', False):
                        # Preserve existing metadata and augment with selected_route
                        step = WorkflowRunStep.objects.get(id=workflow_run_step.id)
                        md = step.metadata or {}
                        md.update({
                            'selected_route': route_value,
                            'use_structured_output_node': True,
                        })
                        # Preserve raw response if different
                        if use_structured and allowed_routes:
                            md.setdefault('raw_response', raw_response)
                        update_kwargs['metadata'] = md
                        logger.debug(
                            "Step %s completed with structured route: '%s'",
                            node.id,
                            route_value,
                        )
                except Exception:
                    # If metadata update fails, continue with status/response only
                    pass

                WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(**update_kwargs)

            await database_sync_to_async(_update_step_completed)()

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
        text_input = await database_sync_to_async(lambda: step_data.text_input or "")()

        if previous_outputs:
            if len(previous_outputs) == 1:
                # Single input - use traditional format
                combined_input = previous_outputs[0].replace(f"Result from {list(context.previous_results.keys())[0]}:\n", "")
                base = f"{prompt_content}\n\nPrevious step result:\n{combined_input}" if prompt_content else combined_input
                if text_input.strip():
                    message = f"{base}\n\nAdditional input:\n{text_input.strip()}"
                else:
                    message = base
            else:
                # Multiple inputs - combine all results
                combined_input = "\n\n".join(previous_outputs)
                base = f"{prompt_content}\n\nResults from previous steps:\n{combined_input}" if prompt_content else combined_input
                if text_input.strip():
                    message = f"{base}\n\nAdditional input:\n{text_input.strip()}"
                else:
                    message = base
        elif context.current_input:
            # Fallback to current_input for backward compatibility
            base = f"{prompt_content}\n\nPrevious step result:\n{context.current_input}" if prompt_content else context.current_input
            if text_input.strip():
                message = f"{base}\n\nAdditional input:\n{text_input.strip()}"
            else:
                message = base
        else:
            base = prompt_content or DefaultValues.DEFAULT_TASK_MESSAGE
            if text_input.strip():
                message = f"{base}\n\nAdditional input:\n{text_input.strip()}"
            else:
                message = base

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

            require_human_validation = await database_sync_to_async(lambda: conditional_data.require_human_validation)()

            # If human validation is required, we still want AI analysis to inform the user
            # So we continue to run the AI evaluation below, then pause for human decision

            llm = await database_sync_to_async(lambda: conditional_data.llm)()

            if not llm:
                # Fallback to first available LLM
                llm = await database_sync_to_async(
                    lambda: LLM.objects.filter(provider=DefaultValues.DEFAULT_LLM_PROVIDER).first()
                )()

            llm_provider = await database_sync_to_async(lambda: llm.provider)()

            evaluation_prompt = await database_sync_to_async(lambda: conditional_data.custom_prompt)()
            evaluation_prompt = evaluation_prompt or "Evaluate the input and choose the appropriate route."

            message = ConditionalPromptService.get_prompt_for_provider(
                provider=llm_provider,
                evaluation_prompt=evaluation_prompt,
                routes=routes,
                input_text=input_output
            )

            user = await database_sync_to_async(lambda: workflow.user)()

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

            routing_decision = None
            analysis_text = None

            try:
                xml_response = f"<root>{full_response.strip()}</root>"
                root = ET.fromstring(xml_response)

                decision_elem = root.find('.//decision')
                if decision_elem is not None and decision_elem.text:
                    routing_decision = decision_elem.text.strip()

                analysis_elem = root.find('.//analysis')
                if analysis_elem is not None and analysis_elem.text:
                    analysis_text = analysis_elem.text.strip()
                    logger.info(f"Conditional node {node.id} analysis: {analysis_text}")

            except ET.ParseError as parse_error:
                logger.warning(f"Failed to parse XML response for node {node.id}: {parse_error}. Raw response: {full_response}")

            route_names = [r['name'] for r in routes]

            if routing_decision not in route_names:
                logger.warning(
                    f"Invalid or missing routing decision '{routing_decision}' for node {node.id}. "
                    f"Valid routes: {route_names}. Defaulting to {routes[0]['name']}."
                )
                routing_decision = routes[0]['name']

            if require_human_validation:
                await database_sync_to_async(
                    lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                        status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
                        response=f"AI recommends: {routing_decision}",
                        metadata={
                            'ai_recommendation': routing_decision,
                            'analysis': analysis_text,
                            'available_routes': [r['name'] for r in routes],
                            'full_response': full_response,
                            'is_human_validated': True,
                            'pending_human_decision': True
                        }
                    )
                )()

                end_time = timezone.now()
                execution_time = (end_time - start_time).total_seconds()

                logger.info(f"Conditional node {node.id} requires human validation. AI recommends: {routing_decision}")

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
                        'step_number': conditional_data.step_number,
                        'custom_prompt': conditional_data.custom_prompt
                    }
                )

            # No human validation required - proceed with AI decision
            # Update workflow run step with results and metadata
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                    status=WorkflowRunStepStatus.COMPLETED,
                    response=routing_decision,
                    metadata={
                        'routing_decision': routing_decision,
                        'analysis': analysis_text,
                        'available_routes': [r['name'] for r in routes],
                        'full_response': full_response,
                        'is_human_validated': False
                    }
                )
            )()

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(f"Successfully executed conditional node {node.id} in {execution_time:.2f}s. Routing: {routing_decision}")

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
                    'full_response': full_response,
                    'is_human_validated': False
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(f"{error_category} in conditional node {node.id} ({error_type}): {str(e)}", exc_info=True)

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
