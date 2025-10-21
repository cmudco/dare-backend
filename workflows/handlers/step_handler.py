"""
Step node handler for workflow execution.

This handler executes LLM calls with configured parameters and handles
structured outputs when connected to StructuredOutput nodes.

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
from core.services.llm_utils import SchemaTransformer

# Import new utility modules
from workflows.handlers.utils import (
    NodeType,
    LLMDefaults,
    ErrorResultBuilder,
    NodeDataValidator,
    StepMessagePreparer,
    RouteResolver,
    RouteNormalizer,
    StructuredOutputBuilder,
    RouteInstructionBuilder,
    MetadataKey,
)


logger = logging.getLogger(__name__)


class StepNodeHandler(BaseExecutionHandler):
    """
    Handler for 'step' type nodes.

    This handler orchestrates step node execution by:
    1. Validating node configuration
    2. Preparing messages using StepMessagePreparer utility
    3. Handling structured output with RouteResolver utilities
    4. Executing LLM queries with proper error handling
    5. Processing billing and status updates via base handler
    6. Normalizing responses for structured routing

    Enhanced with utility modules for better code quality, maintainability,
    and consistency with LLM provider patterns.
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
            workflow_run_step = await self._get_or_create_workflow_run_step(
                context.workflow_run,
                node
            )

            # Update status to running
            await self._update_step_status(
                workflow_run_step,
                WorkflowRunStepStatus.RUNNING
            )

            logger.info(f"[{correlation_id}] Starting step node execution")

            # Prepare message using utility
            message = await self._prepare_message_for_step(step_data, context)

            # Handle structured output configuration
            structured_config = await self._handle_structured_output(
                step_data, node.id, context.workflow_run, message
            )

            # Log debug information
            await self._log_step_debug_info(
                step_data, node.id, structured_config['use_structured']
            )

            # Execute LLM query
            raw_response, token_usage = await self._execute_llm_query(
                step_data=step_data,
                message=structured_config['final_message'],
                context=context,
                workflow_run_step=workflow_run_step,
                structured_spec=structured_config['structured_spec']
            )

            # Normalize response if using structured output
            final_response = await self._normalize_response_if_structured(
                raw_response=raw_response,
                structured_config=structured_config,
                node_id=node.id
            )

            # Process billing using base handler
            await self._process_step_billing(
                step_data, context.workflow_run, node, token_usage
            )

            # Create metadata for structured output
            metadata = self._create_step_metadata(
                final_response, raw_response, structured_config
            )

            # Update workflow run step with results
            await self._update_step_status(
                workflow_run_step=workflow_run_step,
                status=WorkflowRunStepStatus.COMPLETED,
                response=final_response,
                metadata=metadata
            )

            # Calculate execution time
            end_time = timezone.now()
            execution_time = (end_time - start_time).total_seconds()

            logger.info(
                f"[{correlation_id}] Successfully executed step node in {execution_time:.2f}s"
            )

            return NodeExecutionResult(
                success=True,
                output=final_response,
                token_usage=token_usage,
                execution_time=execution_time,
                metadata=metadata
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
        # Get prompt content
        prompt_content = ""
        prompt = await database_sync_to_async(lambda: step_data.prompt)()
        if prompt:
            prompt_content = await database_sync_to_async(lambda: prompt.content)()

        # Get text input
        text_input = await database_sync_to_async(
            lambda: step_data.text_input or ""
        )()

        # Use utility to prepare message
        message = await StepMessagePreparer.prepare_message(
            prompt_content=prompt_content,
            text_input=text_input,
            previous_results=context.previous_results,
            current_input=context.current_input
        )

        return message

    async def _handle_structured_output(
        self,
        step_data: StepNodeData,
        node_id: str,
        workflow_run: WorkflowRun,
        base_message: str
    ) -> Dict[str, Any]:
        """
        Handle structured output configuration for the step.

        Args:
            step_data: Step node configuration
            node_id: Step node ID
            workflow_run: Current workflow run
            base_message: Base message to potentially augment

        Returns:
            Dictionary with structured output configuration:
                - use_structured: bool
                - allowed_routes: List[str]
                - structured_spec: Optional[Dict]
                - final_message: str (possibly augmented with instructions)
        """
        use_structured = await database_sync_to_async(
            lambda: step_data.use_structured_output_node
        )()

        config = {
            'use_structured': use_structured,
            'allowed_routes': [],
            'structured_spec': None,
            'final_message': base_message
        }

        if not use_structured:
            return config

        # Resolve routes using utility
        allowed_routes = await RouteResolver.resolve_routes_for_step(
            workflow_run, node_id
        )

        if not allowed_routes:
            logger.warning(f"Step {node_id} has structured output enabled but no routes found")
            return config

        config['allowed_routes'] = allowed_routes

        # Build structured spec using utility
        config['structured_spec'] = StructuredOutputBuilder.build_structured_spec(
            allowed_routes
        )

        # Check if provider supports native structured output
        llm = await self._get_llm_for_step(step_data)
        llm_provider = await database_sync_to_async(lambda: llm.provider)()

        if not SchemaTransformer.supports_native_structured_output(llm_provider):
            # Fallback: append route instruction to message
            instruction = RouteInstructionBuilder.build_simple_instruction(
                allowed_routes
            )
            config['final_message'] = f"{base_message}{instruction}"
            logger.debug(
                f"Provider {llm_provider} doesn't support native structured output, "
                "added instructions to message"
            )

        return config

    async def _normalize_response_if_structured(
        self,
        raw_response: str,
        structured_config: Dict[str, Any],
        node_id: str
    ) -> str:
        """
        Normalize response if structured output is being used.

        Args:
            raw_response: Raw LLM response
            structured_config: Structured output configuration
            node_id: Node ID for logging

        Returns:
            Normalized response (matches route if structured, else raw)
        """
        if not structured_config['use_structured'] or not structured_config['allowed_routes']:
            return raw_response

        # Use utility for normalization
        normalized, _ = RouteNormalizer.normalize_route_response(
            raw_response,
            structured_config['allowed_routes'],
            node_id
        )

        return normalized

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

    def _create_step_metadata(
        self,
        final_response: str,
        raw_response: str,
        structured_config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Create metadata dictionary for step results.

        Args:
            final_response: Normalized final response
            raw_response: Raw LLM response
            structured_config: Structured output configuration

        Returns:
            Metadata dictionary if structured output used, None otherwise
        """
        if not structured_config['use_structured']:
            return None

        return StructuredOutputBuilder.create_route_metadata(
            selected_route=final_response,
            raw_response=raw_response,
            allowed_routes=structured_config['allowed_routes'],
            use_structured=True
        )

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

        Uses the base LLM service query method for execution.

        Args:
            step_data: Step node configuration
            message: Prepared message for LLM
            context: Execution context
            workflow_run_step: Workflow run step for tracking
            structured_spec: Optional unified structured output specification

        Returns:
            Tuple of (response_text, token_usage)
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

        # Execute LLM query via base service
        response_generator = self.llm_service.query(
            message=message,
            conversation=None,
            llm=llm,
            file_ids=content_file_ids if content_file_ids else None,
            embedding_ids=embedding_file_ids if embedding_file_ids else None,
            user=user,
            prompt_id=prompt_id,
            message_obj=None,
            workflow_run_step_obj=workflow_run_step,
            max_tokens=step_data.max_tokens,
            temperature=step_data.temperature,
            max_context_snippets=step_data.max_context_snippets,
            document_similarity_threshold=step_data.document_similarity_threshold,
            structured_spec=structured_spec,
        )

        # Use base handler to collect response
        return await self._execute_llm_query_with_collection(response_generator)

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

            logger.debug(
                f"Step {step_node_id}: use_structured={use_structured}, "
                f"text_input_len={text_input_len}, "
                f"content_files={content_files_count}, "
                f"embedding_files={embedding_files_count}"
            )

        except Exception as e:
            # Don't break execution if debug logging fails
            logger.warning(f"Failed to log debug info: {e}")
