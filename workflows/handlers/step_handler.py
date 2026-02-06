"""
Step node handler for workflow execution.

This handler executes LLM calls with configured parameters for standard
workflow step nodes.

Refactored to use utility modules following LLM provider patterns.
"""
import logging
from typing import Dict, Optional, Any
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base import ExecutionNode, NodeExecutionContext, NodeExecutionResult
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep, StepNodeData
from workflows.constants import WorkflowRunStepStatus
from conversations.models import LLM
from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.dtos import LLMQueryRequestBuilder

# Import new utility modules
from workflows.handlers.utils import (
    NodeType,
    LLMDefaults,
    ErrorCode,
    ErrorResultBuilder,
    NodeDataValidator,
    StepMessagePreparer,
)
# Import directly from module to avoid circular import via workflows.services
from workflows.services.workflow_web_search_source_service import (
    WorkflowWebSearchSourceService,
)
from workflows.services.citation_serialization import serialize_step_citations


logger = logging.getLogger(__name__)


class StepNodeHandler(BaseExecutionHandler):
    """
    Handler for 'step' type nodes.

    This handler orchestrates step node execution by:
    1. Validating node configuration
    2. Preparing messages using StepMessagePreparer utility
    3. Executing LLM queries with proper error handling
    4. Processing billing and status updates via base handler

    Enhanced with utility modules for better code quality, maintainability,
    and consistency with LLM provider patterns.

    Note: Structured output routing is now handled by the independent
    StructuredOutputNodeHandler, not by step nodes.
    """

    def can_handle(self, node_type: str) -> bool:
        """
        Check if this handler can process the given node type.

        Args:
            node_type: The type of node to check

        Returns:
            True if node_type is 'step', False otherwise
        """
        return node_type == NodeType.STEP

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a step node by calling the LLM with configured parameters.

        This method orchestrates the complete step execution workflow including
        message preparation, structured output handling, LLM execution, billing,
        and result processing.

        Args:
            node: The step node to execute
            context: Execution context with previous results and workflow info

        Returns:
            NodeExecutionResult with LLM response, token usage, and metadata
        """
        start_time = timezone.now()
        correlation_id = f"step-{node.id}"

        try:
            # Validate and get step configuration
            step_data = await self._get_and_validate_step_data(node)
            if step_data is None:
                return ErrorResultBuilder.build_validation_error_result(
                    node_id=node.id,
                    node_type=NodeType.STEP,
                    validation_message="Invalid or missing step node data"
                )

            # Get or create workflow run step for tracking
            # In single step execution mode (manual re-run), reset the step to allow re-execution
            workflow_run_step = await self._get_or_create_workflow_run_step(
                context.workflow_run,
                node,
                reset_if_exists=context.is_single_step_execution
            )

            # Update status to running
            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.RUNNING
            )

            logger.info(f"[{correlation_id}] Starting step node execution")

            # Prepare message using utility
            message = await self._prepare_message_for_step(step_data, context)

            # Log debug information
            await self._log_step_debug_info(step_data, node.id)

            # Send step_started event if streaming callback available
            if context.send_callback:
                try:
                    await context.send_callback(
                        WebSocketResponseService.format_workflow_step_started(
                            node_id=node.id,
                            step_number=node.step_number or 0,
                            node_type="step"
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to send step_started event: {e}")

            # Execute LLM query
            response, token_usage = await self._execute_llm_query(
                step_data=step_data,
                message=message,
                context=context,
                workflow_run_step=workflow_run_step,
                structured_spec=None,
                node_id=node.id
            )

            # Save web search sources if present
            if token_usage and token_usage.get("web_search_sources"):
                await WorkflowWebSearchSourceService.save_sources(
                    workflow_run_step=workflow_run_step,
                    sources=token_usage["web_search_sources"],
                )

            # Process billing using base handler
            await self._process_step_billing(
                step_data, context.workflow_run, node, token_usage
            )

            # Update workflow run step with results
            await self._update_step_status(
                workflow_run_step=workflow_run_step,
                status=WorkflowRunStepStatus.COMPLETED,
                response=response,
                metadata=None
            )

            # Calculate execution time
            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Successfully executed step node in {execution_time:.2f}s"
            )

            # Send step_completed event if streaming callback available
            if context.send_callback:
                try:
                    # Serialize citation data for the step_completed event
                    snippets_data, web_sources_data = await database_sync_to_async(
                        lambda: serialize_step_citations(workflow_run_step)
                    )()

                    await context.send_callback(
                        WebSocketResponseService.format_workflow_step_completed(
                            node_id=node.id,
                            response=response,
                            status="completed",
                            tokens={
                                "input": token_usage.get("input_tokens", 0),
                                "output": token_usage.get("output_tokens", 0)
                            } if token_usage else None,
                            metadata={
                                "snippets": snippets_data,
                                "webSearchSources": web_sources_data,
                            } if snippets_data or web_sources_data else None
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to send step_completed event: {e}")

            return NodeExecutionResult(
                success=True,
                output=response,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata=None
            )

        except Exception as e:
            # Use utility for error handling
            logger.error(
                f"[{correlation_id}] Step node execution failed: {str(e)}",
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

    async def _get_and_validate_step_data(
        self, node: ExecutionNode
    ) -> Optional[StepNodeData]:
        """
        Get and validate step node data.

        Args:
            node: The execution node

        Returns:
            StepNodeData if valid, None otherwise
        """
        step_data = await database_sync_to_async(
            lambda: node.db_node.data_object
        )()

        if not NodeDataValidator.validate_node_data_type(
            step_data, StepNodeData, node.id
        ):
            return None

        return step_data

    async def _prepare_message_for_step(
        self,
        step_data: StepNodeData,
        context: NodeExecutionContext
    ) -> str:
        """
        Prepare message for LLM using StepMessagePreparer utility.

        Args:
            step_data: Step node configuration
            context: Execution context

        Returns:
            Formatted message ready for LLM processing
        """
        # Batch DB access: prompt content + text input in a single call
        def _get_message_inputs():
            prompt = step_data.prompt
            return {
                'prompt_content': prompt.content if prompt else "",
                'text_input': step_data.text_input or "",
            }

        inputs = await database_sync_to_async(_get_message_inputs)()
        prompt_content = inputs['prompt_content']
        text_input = inputs['text_input']

        # Use utility to prepare message
        message = await StepMessagePreparer.prepare_message(
            prompt_content=prompt_content,
            text_input=text_input,
            previous_results=context.previous_results
            # REMOVED: current_input parameter (use edge-based data flow)
        )

        return message

    async def _process_step_billing(
        self,
        step_data: StepNodeData,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        token_usage: Optional[Dict]
    ):
        """
        Process billing for the step execution.

        Args:
            step_data: Step node configuration
            workflow_run: Current workflow run
            node: Execution node
            token_usage: Token usage from LLM call
        """
        user = await self._get_user_from_workflow_run(workflow_run)
        llm = await self._get_llm_for_step(step_data)

        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node.db_node.id
        )

    async def _execute_llm_query(
        self,
        step_data: StepNodeData,
        message: str,
        context: NodeExecutionContext,
        workflow_run_step: WorkflowRunStep,
        structured_spec: Optional[Dict],
        node_id: Optional[str] = None
    ) -> tuple[str, Dict]:
        """
        Execute LLM query and collect response.

        Uses the base LLM service query method for execution.
        Supports real-time streaming via context.send_callback.

        Args:
            step_data: Step node configuration
            message: Prepared message for LLM
            context: Execution context (includes send_callback for streaming)
            workflow_run_step: Workflow run step for tracking
            structured_spec: Optional unified structured output specification
            node_id: Node ID for streaming events

        Returns:
            Tuple of (response_text, token_usage)
        """
        # Get LLM configuration
        llm = await self._get_llm_for_step(step_data)

        # Batch database queries for better performance
        step_config = await self._get_step_execution_config(step_data, context)

        # Execute LLM query via base service using DTO builder
        request = LLMQueryRequestBuilder.from_workflow_data(
            message=message,
            user=step_config['user'],
            llm=llm,
            file_ids=step_config['content_file_ids'] if step_config['content_file_ids'] else None,
            embedding_ids=step_config['embedding_file_ids'] if step_config['embedding_file_ids'] else None,
            prompt_id=step_config['prompt_id'],
            temperature=step_data.temperature,
            max_tokens=step_data.max_tokens,
            max_context_snippets=step_data.max_context_snippets,
            document_similarity_threshold=step_data.document_similarity_threshold,
            workflow_run_step_obj=workflow_run_step,
            structured_spec=structured_spec,
            web_search_enabled=step_config['enable_web_search'],
        )

        response_generator = self.llm_service.query(request)

        # Use base handler to collect response (with streaming if callback provided)
        return await self._execute_llm_query_with_collection(
            response_generator,
            send_callback=context.send_callback,
            node_id=node_id
        )

    async def _get_step_execution_config(
        self,
        step_data: StepNodeData,
        context: NodeExecutionContext
    ) -> Dict[str, Any]:
        """
        Batch database queries for step execution configuration.

        Combines multiple database calls into a single async operation for better performance.

        Args:
            step_data: Step node configuration
            context: Execution context with workflow run info

        Returns:
            Dictionary containing:
                - user: User instance
                - content_file_ids: List of content file IDs
                - embedding_file_ids: List of embedding file IDs
                - prompt_id: Prompt ID if available
                - enable_web_search: Web search enabled flag
        """
        def _get_config():
            workflow = context.workflow_run.workflow
            return {
                'user': workflow.user,
                'content_file_ids': list(step_data.content_files.values_list('id', flat=True)),
                'embedding_file_ids': list(step_data.embedding_files.values_list('id', flat=True)),
                'prompt_id': step_data.prompt.id if step_data.prompt else None,
                'enable_web_search': step_data.enable_web_search,
            }

        return await database_sync_to_async(_get_config)()

    async def _get_llm_for_step(self, step_data: StepNodeData) -> LLM:
        """
        Get the LLM to use for this step.

        Returns the LLM configured for this step, or falls back to the first
        available default provider model if no LLM is specifically configured.

        Args:
            step_data: Step node configuration

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        llm = await database_sync_to_async(lambda: step_data.llm)()

        if llm:
            return llm

        # Fallback to default provider
        logger.warning(
            f"No LLM configured for step, falling back to {LLMDefaults.DEFAULT_PROVIDER}"
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

    async def _log_step_debug_info(
        self,
        step_data: StepNodeData,
        step_node_id: str
    ):
        """
        Log debug information about step configuration.

        Args:
            step_data: Step node configuration
            step_node_id: Step node ID
        """
        try:
            text_input_len = len(step_data.text_input or '')
            content_files_count = await database_sync_to_async(
                lambda: step_data.content_files.count()
            )()
            embedding_files_count = await database_sync_to_async(
                lambda: step_data.embedding_files.count()
            )()

            logger.debug(
                f"Step {step_node_id}: "
                f"text_input_len={text_input_len}, "
                f"content_files={content_files_count}, "
                f"embedding_files={embedding_files_count}"
            )

        except Exception as e:
            # Don't break execution if debug logging fails
            logger.warning(f"Failed to log debug info: {e}")
