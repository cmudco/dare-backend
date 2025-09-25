"""
Node type handlers for workflow execution.

This module provides specialized handlers for different types of workflow nodes,
including step nodes, aggregator nodes, and output nodes. Each handler encapsulates
the specific logic and requirements for executing that node type.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, AsyncGenerator, Tuple
from dataclasses import dataclass

from django.utils import timezone
from channels.db import database_sync_to_async

from workflows.models import (
    WorkflowNode, WorkflowRun, WorkflowRunStep,
    StepNodeData, AggregatorNodeData, ChatOutputNodeData, StartNodeData
)
from workflows.constants import WorkflowRunStepStatus
# ExecutionNode is now defined locally
from core.services.llm_service import LLMService
from conversations.models import LLM


logger = logging.getLogger(__name__)


@dataclass
class ExecutionNode:
    """Simplified node representation for execution planning."""
    id: str
    type: str  # 'start', 'step', 'chatOutput', 'aggregator'
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
            logger.error(f"Failed to execute step node {node.id}: {str(e)}", exc_info=True)

            # Update workflow run step with error
            try:
                workflow_run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)
                await database_sync_to_async(
                    lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                        status=WorkflowRunStepStatus.FAILED,
                        error=str(e)
                    )
                )()
            except:
                pass

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            return NodeExecutionResult(
                success=False,
                error=str(e),
                execution_time=execution_time
            )

    async def _prepare_message(self, step_data: StepNodeData, context: NodeExecutionContext) -> str:
        """Prepare the message for LLM based on step configuration and context."""
        # Get base prompt content using async wrapper for Django ORM access
        prompt_content = ""
        prompt = await database_sync_to_async(lambda: step_data.prompt)()
        if prompt:
            prompt_content = await database_sync_to_async(lambda: prompt.content)()

        # Add previous context if available
        if context.current_input:
            if prompt_content:
                message = f"{prompt_content}\n\nPrevious step result:\n{context.current_input}"
            else:
                message = context.current_input
        else:
            message = prompt_content or "Please proceed with the task."

        return message

    async def _get_llm_for_step(self, step_data: StepNodeData) -> LLM:
        """Get the LLM to use for this step."""
        llm = await database_sync_to_async(lambda: step_data.llm)()
        if not llm:
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider="openai").first()
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


class AggregatorNodeHandler(BaseNodeHandler):
    """Handler for 'aggregator' type nodes - combines and evaluates multiple inputs."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == 'aggregator'

    async def execute(self, node: ExecutionNode, context: NodeExecutionContext) -> NodeExecutionResult:
        """Execute an aggregator node by combining multiple inputs and evaluating them."""
        start_time = timezone.now()
        print(f"🔄 AGGREGATOR NODE EXECUTION STARTED - {node.id}")

        try:
            # Get aggregator data
            aggregator_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not aggregator_data or not isinstance(aggregator_data, AggregatorNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid aggregator node data"
                )

            # Collect all previous results for aggregation
            previous_outputs = []
            for result_key, result_data in context.previous_results.items():
                if isinstance(result_data, dict) and 'output' in result_data:
                    previous_outputs.append(f"Result from {result_key}: {result_data['output']}")

            if not previous_outputs:
                return NodeExecutionResult(
                    success=False,
                    error="No previous results to aggregate"
                )

            # Prepare aggregation message based on scoring mode
            combined_input = "\n\n".join(previous_outputs)
            evaluation_prompt = aggregator_data.custom_prompt or (
                "Evaluate the quality of the responses and provide a score based on accuracy, relevance, and clarity."
            )

            if aggregator_data.scoring_mode == 'quantitative':
                # For quantitative scoring, expect numerical output for routing
                scoring_instruction = """Provide a quantitative score from 0-100 and categorize it:
- 0-40: bad
- 41-70: average
- 71-100: good

End your response with: ROUTING_DECISION: [good|bad|average]"""
            else:
                # For qualitative scoring, expect true/false decision
                scoring_instruction = """Provide a qualitative assessment and make a decision:
- If the results pass your evaluation criteria, respond with true
- If the results fail your evaluation criteria, respond with false

End your response with: ROUTING_DECISION: [true|false]"""

            message = f"""{evaluation_prompt}

{scoring_instruction}

Please evaluate the following results:

{combined_input}

Provide your evaluation with reasoning and end with the routing decision."""

            # Get default LLM for aggregation - try Claude first to avoid OpenAI connection issues
            print(f"📋 Getting LLM for aggregation...")
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider="claude").first()
            )()

            if not llm:
                print(f"⚠️  No Claude LLM found, falling back to OpenAI...")
                llm = await database_sync_to_async(
                    lambda: LLM.objects.filter(provider="openai").first()
                )()

            print(f"🤖 Selected LLM: {llm.identifier if llm else 'None'} ({llm.provider if llm else 'None'})")

            # Get user for LLM service
            workflow = await database_sync_to_async(lambda: context.workflow_run.workflow)()
            user = await database_sync_to_async(lambda: workflow.user)()
            print(f"👤 User: {user.id}")

            print(f"🚀 Starting LLM query for aggregation...")

            # Execute LLM query for aggregation
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
                max_tokens=2048,
                temperature=0.3  # Lower temperature for more consistent evaluation
            )

            print(f"📡 LLM query initiated, collecting response...")

            # Collect response with proper error handling
            full_response = ""
            token_usage = {}
            chunk_count = 0

            try:
                async for chunk, usage in response_generator:
                    chunk_count += 1
                    if chunk:  # Only add non-empty chunks
                        full_response += chunk
                    if usage:
                        token_usage = usage
                    if chunk_count % 10 == 0:  # Log every 10 chunks
                        print(f"📥 Received {chunk_count} chunks, current response length: {len(full_response)}")

                print(f"✅ Response collection complete: {chunk_count} chunks, {len(full_response)} chars")

            except Exception as stream_error:
                print(f"💥 Error during response streaming: {stream_error}")
                raise  # Re-raise to be caught by outer exception handler

            # Extract routing decision from response
            routing_decision = self._extract_routing_decision(full_response, aggregator_data.scoring_mode)

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(f"Successfully executed aggregator node {node.id} in {execution_time:.2f}s. Routing: {routing_decision}")

            return NodeExecutionResult(
                success=True,
                output=full_response,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata={
                    'scoring_mode': aggregator_data.scoring_mode,
                    'aggregated_results_count': len(previous_outputs),
                    'routing_decision': routing_decision
                }
            )

        except Exception as e:
            logger.error(f"Failed to execute aggregator node {node.id}: {str(e)}", exc_info=True)

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            return NodeExecutionResult(
                success=False,
                error=str(e),
                execution_time=execution_time
            )

    def _extract_routing_decision(self, response: str, scoring_mode: str) -> str:
        """Extract routing decision from aggregator response."""
        try:
            # Look for ROUTING_DECISION: pattern
            import re
            pattern = r'ROUTING_DECISION:\s*(\w+)'
            match = re.search(pattern, response, re.IGNORECASE)

            if match:
                decision = match.group(1).lower()

                # Validate decision based on scoring mode
                if scoring_mode == 'quantitative':
                    valid_decisions = ['good', 'bad', 'average']
                else:  # qualitative
                    valid_decisions = ['true', 'false']

                if decision in valid_decisions:
                    return decision

            # Fallback: try to extract from common patterns
            response_lower = response.lower()

            if scoring_mode == 'quantitative':
                if 'good' in response_lower or 'excellent' in response_lower:
                    return 'good'
                elif 'bad' in response_lower or 'poor' in response_lower or 'fail' in response_lower:
                    return 'bad'
                else:
                    return 'average'
            else:  # qualitative
                if 'true' in response_lower or 'pass' in response_lower or 'yes' in response_lower:
                    return 'true'
                else:
                    return 'false'

        except Exception:
            # Default fallback
            return 'average' if scoring_mode == 'quantitative' else 'false'


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
            logger.error(f"Failed to execute output node {node.id}: {str(e)}", exc_info=True)

            return NodeExecutionResult(
                success=False,
                error=str(e)
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
            logger.error(f"Failed to execute start node {node.id}: {str(e)}", exc_info=True)

            return NodeExecutionResult(
                success=False,
                error=str(e)
            )


class NodeHandlerRegistry:
    """Registry for managing node type handlers."""

    def __init__(self):
        """Initialize the registry with default handlers."""
        self._handlers: List[BaseNodeHandler] = []
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register the default node handlers."""
        self.register_handler(StepNodeHandler())
        self.register_handler(AggregatorNodeHandler())
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