"""
Step node handler for workflow execution.

This handler executes LLM calls with configured parameters and handles
structured outputs when connected to StructuredOutput nodes.
"""
import logging
from typing import Dict, Optional
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.structured_output_handler import StructuredOutputHandler
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep, StepNodeData
from workflows.constants import WorkflowRunStepStatus
from workflows.node_handler_constants import DefaultValues
from core.services.billing_service import BillingService
from conversations.models import LLM


logger = logging.getLogger(__name__)


class StepNodeHandler(BaseNodeHandler):
    """
    Handler for 'step' type nodes.

    This handler:
    1. Prepares messages from prompts and context
    2. Handles structured output configuration if applicable
    3. Executes LLM query with appropriate parameters
    4. Processes and normalizes responses
    5. Handles billing for token usage
    6. Updates workflow run step status

    The handler supports both regular text output and structured routing output.
    """

    def __init__(self):
        """Initialize with LLM service and structured output handler."""
        super().__init__()
        self.structured_handler = StructuredOutputHandler()

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'step' nodes."""
        return node_type == 'step'

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a step node by calling the LLM with configured parameters.

        Args:
            node: The step node to execute
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with LLM response and metadata
        """
        start_time = timezone.now()

        try:
            # Get step configuration
            step_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not step_data or not isinstance(step_data, StepNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid step node data"
                )

            # Get or create workflow run step
            workflow_run_step = await self._get_or_create_workflow_run_step(
                context.workflow_run,
                node
            )

            # Update status to running
            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.RUNNING
            )

            # Prepare message for LLM
            message = await self._prepare_message(step_data, context)

            # Handle structured output configuration
            use_structured = await database_sync_to_async(
                lambda: step_data.use_structured_output_node
            )()

            allowed_routes = []
            structured_spec = None

            if use_structured:
                allowed_routes = await self.structured_handler.resolve_routes_for_step(
                    context.workflow_run,
                    node.id
                )

                if allowed_routes:
                    # Add routing instructions to message
                    route_instruction = self.structured_handler.build_route_instruction(
                        allowed_routes
                    )
                    message = f"{message}{route_instruction}"

                    # Build structured spec for LLM services that support it
                    structured_spec = self.structured_handler.build_structured_spec(
                        allowed_routes
                    )

            # Log debug information
            await self._log_step_debug_info(step_data, node.id, use_structured)

            # Execute LLM query
            raw_response, token_usage = await self._execute_llm_query(
                step_data=step_data,
                message=message,
                context=context,
                workflow_run_step=workflow_run_step,
                structured_spec=structured_spec
            )

            # Normalize response if using structured output
            final_response = raw_response
            if use_structured and allowed_routes:
                final_response, _ = self.structured_handler.normalize_route_response(
                    raw_response,
                    allowed_routes,
                    node.id
                )

            # Process billing
            await self._process_billing(
                token_usage=token_usage,
                step_data=step_data,
                node=node,
                workflow_run=context.workflow_run
            )

            # Update workflow run step with results
            await self._update_step_completed(
                workflow_run_step=workflow_run_step,
                response=final_response,
                raw_response=raw_response,
                use_structured=use_structured,
                allowed_routes=allowed_routes,
                step_data=step_data
            )

            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"Successfully executed step node {node.id} in {execution_time:.2f}s"
            )

            return NodeExecutionResult(
                success=True,
                output=final_response,
                token_usage=token_usage,
                execution_time=execution_time
            )

        except Exception as e:
            return await self._handle_execution_error(
                e, node, context, workflow_run_step, start_time
            )

    async def _prepare_message(
        self,
        step_data: StepNodeData,
        context: NodeExecutionContext
    ) -> str:
        """
        Prepare the message for LLM based on step configuration and context.

        This method combines:
        - Step's prompt content
        - Previous step results
        - Text input from step configuration

        Args:
            step_data: Step node configuration
            context: Execution context with previous results

        Returns:
            Formatted message ready for LLM processing
        """
        # Get base prompt content
        prompt_content = ""
        prompt = await database_sync_to_async(lambda: step_data.prompt)()
        if prompt:
            prompt_content = await database_sync_to_async(lambda: prompt.content)()

        # Get text input from step configuration
        text_input = await database_sync_to_async(
            lambda: step_data.text_input or ""
        )()

        # Collect previous outputs from direct dependencies
        previous_outputs = []
        if context.previous_results:
            for node_id, result_data in context.previous_results.items():
                if self._is_valid_result(result_data):
                    previous_outputs.append(f"Result from {node_id}:\n{result_data['output']}")

        # Build message based on available inputs
        if previous_outputs:
            if len(previous_outputs) == 1:
                # Single input - use traditional format
                combined_input = previous_outputs[0].replace(
                    f"Result from {list(context.previous_results.keys())[0]}:\n", ""
                )
                base = self._combine_prompt_and_input(prompt_content, combined_input, "Previous step result")
            else:
                # Multiple inputs - combine all results
                combined_input = "\n\n".join(previous_outputs)
                base = self._combine_prompt_and_input(prompt_content, combined_input, "Results from previous steps")

            # Add text input if present
            message = self._add_additional_input(base, text_input)

        elif context.current_input:
            # Fallback to current_input for backward compatibility
            base = self._combine_prompt_and_input(prompt_content, context.current_input, "Previous step result")
            message = self._add_additional_input(base, text_input)

        else:
            # No previous input - use prompt and text input
            base = prompt_content or DefaultValues.DEFAULT_TASK_MESSAGE
            message = self._add_additional_input(base, text_input)

        return message

    def _is_valid_result(self, result_data: Dict) -> bool:
        """
        Check if result data is valid and not skipped.

        Args:
            result_data: Result data dictionary

        Returns:
            bool: True if result is valid and not skipped
        """
        if not result_data or not isinstance(result_data, dict):
            return False

        if not result_data.get('output'):
            return False

        metadata = result_data.get('metadata') or {}
        is_skipped = metadata.get('skipped', False)

        return not is_skipped

    def _combine_prompt_and_input(
        self,
        prompt_content: str,
        input_text: str,
        input_label: str
    ) -> str:
        """
        Combine prompt content with input text.

        Args:
            prompt_content: Base prompt text
            input_text: Input to combine
            input_label: Label for the input section

        Returns:
            Combined text
        """
        if prompt_content:
            return f"{prompt_content}\n\n{input_label}:\n{input_text}"
        return input_text

    def _add_additional_input(self, base: str, text_input: str) -> str:
        """
        Add additional text input to base message.

        Args:
            base: Base message
            text_input: Additional text input

        Returns:
            Message with additional input if present
        """
        if text_input.strip():
            return f"{base}\n\nAdditional input:\n{text_input.strip()}"
        return base

    async def _execute_llm_query(
        self,
        step_data: StepNodeData,
        message: str,
        context: NodeExecutionContext,
        workflow_run_step: WorkflowRunStep,
        structured_spec: Optional[Dict]
    ) -> tuple[str, Dict]:
        """
        Execute LLM query and collect response.

        Args:
            step_data: Step node configuration
            message: Prepared message for LLM
            context: Execution context
            workflow_run_step: Workflow run step for tracking
            structured_spec: Optional structured output specification

        Returns:
            tuple: (response_text, token_usage)
        """
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
        workflow = await database_sync_to_async(
            lambda: context.workflow_run.workflow
        )()
        user = await database_sync_to_async(lambda: workflow.user)()
        prompt_id = await database_sync_to_async(
            lambda: step_data.prompt.id if step_data.prompt else None
        )()

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
            document_similarity_threshold=step_data.document_similarity_threshold,
            structured_spec=structured_spec,
        )

        # Collect response
        full_response = ""
        token_usage = {}

        async for chunk, usage in response_generator:
            full_response += chunk
            if usage:
                token_usage = usage

        return full_response, token_usage

    async def _process_billing(
        self,
        token_usage: Dict,
        step_data: StepNodeData,
        node: ExecutionNode,
        workflow_run: WorkflowRun
    ):
        """
        Process billing for token usage.

        Args:
            token_usage: Token usage statistics
            step_data: Step node configuration
            node: Execution node
            workflow_run: Current workflow run
        """
        if not token_usage or not token_usage.get('input_tokens') or not token_usage.get('output_tokens'):
            return

        try:
            billing_service = BillingService()

            user = await database_sync_to_async(lambda: workflow_run.user)()

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
                logger.warning(
                    f"Billing failed for step {node.id}, but continuing execution"
                )

        except Exception as billing_error:
            logger.error(f"Billing error for step {node.id}: {str(billing_error)}")
            # Continue execution even if billing fails

    async def _update_step_completed(
        self,
        workflow_run_step: WorkflowRunStep,
        response: str,
        raw_response: str,
        use_structured: bool,
        allowed_routes: list,
        step_data: StepNodeData
    ):
        """
        Update workflow run step as completed.

        Args:
            workflow_run_step: Workflow run step to update
            response: Final response (normalized if structured)
            raw_response: Raw LLM response
            use_structured: Whether structured output was used
            allowed_routes: List of allowed routes
            step_data: Step node configuration
        """
        def _update():
            update_kwargs = {
                'status': WorkflowRunStepStatus.COMPLETED,
                'response': response,
            }

            try:
                if use_structured:
                    # Build metadata for structured output
                    metadata = self.structured_handler.create_metadata_for_step(
                        selected_route=response,
                        raw_response=raw_response,
                        allowed_routes=allowed_routes,
                        use_structured=True
                    )

                    # Preserve existing metadata and merge
                    step = WorkflowRunStep.objects.get(id=workflow_run_step.id)
                    existing_metadata = step.metadata or {}
                    existing_metadata.update(metadata)
                    update_kwargs['metadata'] = existing_metadata

            except Exception as e:
                # If metadata update fails, continue with status/response only
                logger.warning(f"Failed to update metadata: {e}")

            WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(**update_kwargs)

        await database_sync_to_async(_update)()

    async def _get_llm_for_step(self, step_data: StepNodeData) -> LLM:
        """
        Get the LLM to use for this step.

        Returns the LLM configured for this step, or falls back to the first
        available OpenAI model if no LLM is specifically configured.

        Args:
            step_data: Step node configuration

        Returns:
            LLM instance
        """
        llm = await database_sync_to_async(lambda: step_data.llm)()

        if not llm:
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(
                    provider=DefaultValues.DEFAULT_LLM_PROVIDER
                ).first()
            )()

        return llm

    async def _log_step_debug_info(
        self,
        step_data: StepNodeData,
        step_node_id: str,
        use_structured: bool
    ):
        """
        Log debug information about step configuration.

        Args:
            step_data: Step node configuration
            step_node_id: Step node ID
            use_structured: Whether structured output is enabled
        """
        try:
            text_input_len = len(step_data.text_input or '')
            content_files_count = await database_sync_to_async(
                lambda: step_data.content_files.count()
            )()
            embedding_files_count = await database_sync_to_async(
                lambda: step_data.embedding_files.count()
            )()

            self.structured_handler.log_structured_output_debug(
                step_node_id=step_node_id,
                use_structured=use_structured,
                text_input_len=text_input_len,
                content_files_count=content_files_count,
                embedding_files_count=embedding_files_count
            )

        except Exception:
            # Don't break execution if debug logging fails
            pass

    async def _update_step_status(
        self,
        workflow_run_step: WorkflowRunStep,
        status: str
    ):
        """
        Update workflow run step status.

        Args:
            workflow_run_step: Workflow run step to update
            status: New status
        """
        await database_sync_to_async(
            lambda: WorkflowRunStep.objects.filter(id=workflow_run_step.id).update(
                status=status
            )
        )()

    async def _handle_execution_error(
        self,
        exception: Exception,
        node: ExecutionNode,
        context: NodeExecutionContext,
        workflow_run_step: WorkflowRunStep,
        start_time
    ) -> NodeExecutionResult:
        """
        Handle execution errors and update workflow run step.

        Args:
            exception: The exception that occurred
            node: Execution node
            context: Execution context
            workflow_run_step: Workflow run step to update
            start_time: Execution start time

        Returns:
            NodeExecutionResult with error information
        """
        error_category, error_type = categorize_error(exception)
        error_msg = f"{error_category}: {str(exception)}"
        logger.error(
            f"{error_category} in step {node.id} ({error_type}): {str(exception)}",
            exc_info=True
        )

        # Update workflow run step with error
        try:
            workflow_run_step = await self._get_or_create_workflow_run_step(
                context.workflow_run,
                node
            )
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

    @database_sync_to_async
    def _get_or_create_workflow_run_step(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode
    ) -> WorkflowRunStep:
        """
        Get or create a WorkflowRunStep for the step node.

        Args:
            workflow_run: The workflow run instance
            node: The execution node

        Returns:
            WorkflowRunStep instance
        """
        step, created = WorkflowRunStep.objects.get_or_create(
            workflow_run=workflow_run,
            step_node=node.db_node,
            defaults={
                'order': node.step_number or 0,
                'status': WorkflowRunStepStatus.PENDING
            }
        )
        return step
