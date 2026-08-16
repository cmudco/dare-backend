"""
Step node handler for workflow execution.

Executes LLM calls with configured parameters. Pipeline pattern:
    validate → init run step → mark running → prepare message →
    call LLM → save citations → bill → mark completed → return result
"""
import logging
from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.models import LLM
from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import LLMQueryRequestBuilder
from dare_tools.services.retrieval_tool_executor import RetrievalScope
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.base import (ExecutionNode, NodeExecutionContext,
                                     NodeExecutionResult)
from workflows.handlers.event_emitter import EventEmitter
from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.utils import (LLMDefaults, NodeDataValidator, NodeType,
                                      StepMessagePreparer)
from workflows.models import StepNodeData, WorkflowRun, WorkflowRunStep
from workflows.services.citation_serialization import (
    serialize_step_artifacts, serialize_step_citations,
    serialize_step_tool_calls)
from workflows.services.tool_loop_binding import WorkflowToolLoopBinding
from workflows.services.workflow_web_search_source_service import \
    WorkflowWebSearchSourceService

logger = logging.getLogger(__name__)


class StepNodeHandler(BaseExecutionHandler):
    """Handler for 'step' type nodes."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_loop_service = ToolLoopService(self.llm_service)

    def can_handle(self, node_type: str) -> bool:
        return node_type == NodeType.STEP

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """
        Execute a step node as a clean pipeline:
        validate → init → start → prepare → LLM → citations → bill → complete
        """
        start_time = timezone.now()
        emitter = EventEmitter(context.send_callback, workflow_run_id=context.workflow_run.id)

        try:
            step_data = await self._validate(node)
            run_step = await self._init_run_step(context, node)
            started_at = await self._mark_running(run_step)
            await emitter.step_started(node.id, node.label, "step", started_at)

            message = await self._prepare_message(step_data, context)
            response, token_usage = await self._call_llm(step_data, message, context, run_step, emitter, node.id)

            await self._save_web_search_sources(run_step, token_usage)
            await self._bill(step_data, context.workflow_run, node, token_usage)
            await self._mark_completed(run_step, response)

            execution_time = (timezone.now() - start_time).total_seconds()

            await self._emit_completed(emitter, node.id, response, token_usage, run_step)

            return NodeExecutionResult(
                success=True,
                output=response,
                token_usage=token_usage,
                execution_time=execution_time,
            )

        except Exception as e:
            logger.error(f"Step node {node.id} failed: {e}", exc_info=True)
            await self._handle_failure(context, node, e)
            return self._build_error_result(e, node, start_time)

    # ==================== Pipeline Steps ====================

    async def _validate(self, node: ExecutionNode) -> StepNodeData:
        """Validate and return the StepNodeData. Raises on invalid data."""
        step_data = await database_sync_to_async(lambda: node.db_node.data_object)()

        if not NodeDataValidator.validate_node_data_type(step_data, StepNodeData, node.id):
            raise ValueError(f"Invalid or missing step node data for node {node.id}")

        return step_data

    async def _init_run_step(
        self,
        context: NodeExecutionContext,
        node: ExecutionNode,
    ) -> WorkflowRunStep:
        """Get or create the WorkflowRunStep, resetting if manual re-run."""
        return await self._get_or_create_workflow_run_step(
            context.workflow_run,
            node,
            reset_if_exists=context.is_single_step_execution,
        )

    async def _mark_running(self, run_step: WorkflowRunStep):
        """Transition step to RUNNING status. Returns started_at timestamp."""
        return await self._update_step_status(run_step, WorkflowRunStepStatus.RUNNING)

    async def _prepare_message(
        self,
        step_data: StepNodeData,
        context: NodeExecutionContext,
    ) -> str:
        """Prepare the message for the LLM from prompt + text_input + previous results."""
        def _get_message_inputs():
            prompt = step_data.prompt
            return {
                'prompt_content': prompt.content if prompt else "",
                'text_input': step_data.text_input or "",
                'include_context': step_data.use_previous_context,
            }

        inputs = await database_sync_to_async(_get_message_inputs)()

        return await StepMessagePreparer.prepare_message(
            prompt_content=inputs['prompt_content'],
            text_input=inputs['text_input'],
            previous_results=context.previous_results,
            include_context=inputs['include_context'],
        )

    async def _call_llm(
        self,
        step_data: StepNodeData,
        message: str,
        context: NodeExecutionContext,
        run_step: WorkflowRunStep,
        emitter: EventEmitter,
        node_id: str,
    ) -> tuple[str, Dict]:
        """Build the LLM request and stream the response."""
        llm = await self._get_llm_for_step(step_data)
        config = await self._get_step_execution_config(step_data, context)

        request = LLMQueryRequestBuilder.from_workflow_data(
            message=message,
            user=config['user'],
            llm=llm,
            file_ids=config['content_file_ids'] or None,
            embedding_ids=config['embedding_file_ids'] or None,
            tag_ids=config['tag_ids'] or None,
            prompt_id=config['prompt_id'],
            temperature=step_data.temperature,
            max_tokens=step_data.max_tokens,
            max_context_snippets=step_data.max_context_snippets,
            document_similarity_threshold=step_data.document_similarity_threshold,
            workflow_run_step_obj=run_step,
            structured_spec=None,
            web_search_enabled=config['enable_web_search'],
            file_owner_id=None,
            rag_mode=config['rag_mode'],
            library_ids=config['library_ids'] or None,
            web_fetch_enabled=config['enable_web_fetch'],
            mcp_server_ids=config['mcp_server_ids'] or None,
            artifacts_enabled=config['enable_artifacts'],
        )

        retrieval_scope = RetrievalScope(
            embedding_ids=tuple(request.context.embedding_ids or ()),
            tag_ids=tuple(request.context.tag_ids or ()),
            folder_ids=tuple(request.context.folder_ids or ()),
            library_ids=tuple(request.context.library_ids or ()),
            user_id=config['user'].id,
            file_owner_id=request.context.file_owner_id,
            max_context_snippets=request.context.max_context_snippets,
            similarity_threshold=request.context.document_similarity_threshold,
        )
        binding = WorkflowToolLoopBinding(
            run_step=run_step,
            node_id=node_id,
            user=config['user'],
            emitter=emitter,
            send_callback=context.send_callback,
        )

        # regenerate=True: a re-run reuses the same WorkflowRunStep, so its
        # previous tool-call rows must never survive into the new turn.
        result = await self.tool_loop_service.run(
            request=request,
            binding=binding,
            retrieval_scope=retrieval_scope,
            regenerate=True,
        )

        if result.timed_out:
            raise TimeoutError(
                "The model stream stalled mid-step; the step was aborted."
            )

        return result.text, result.token_usage or {}

    async def _save_web_search_sources(
        self,
        run_step: WorkflowRunStep,
        token_usage: Dict,
    ) -> None:
        """Save web search sources if the LLM returned any."""
        if token_usage and token_usage.get("web_search_sources"):
            await WorkflowWebSearchSourceService.save_sources(
                workflow_run_step=run_step,
                sources=token_usage["web_search_sources"],
            )

    async def _bill(
        self,
        step_data: StepNodeData,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
        token_usage: Optional[Dict],
    ) -> None:
        """Process billing for the step execution."""
        user = await self._get_user_from_workflow_run(workflow_run)
        llm = await self._get_llm_for_step(step_data)
        await self._process_billing(
            token_usage=token_usage,
            llm=llm,
            user=user,
            step_node_id=node.db_node.id,
        )

    async def _mark_completed(
        self,
        run_step: WorkflowRunStep,
        response: str,
    ) -> None:
        """Transition step to COMPLETED with the final response."""
        await self._update_step_status(
            run_step,
            WorkflowRunStepStatus.COMPLETED,
            response=response,
        )

    async def _emit_completed(
        self,
        emitter: EventEmitter,
        node_id: str,
        response: str,
        token_usage: Dict,
        run_step: WorkflowRunStep,
    ) -> None:
        """Emit step_completed event with citation and tool-call metadata."""
        def _serialize():
            snippets, web_sources = serialize_step_citations(run_step)
            tool_calls = serialize_step_tool_calls(run_step)
            artifacts = serialize_step_artifacts(run_step)
            return snippets, web_sources, tool_calls, artifacts

        (
            snippets_data,
            web_sources_data,
            tool_calls_data,
            artifacts_data,
        ) = await database_sync_to_async(_serialize)()

        tokens = None
        if token_usage:
            tokens = {
                "input": token_usage.get("input_tokens", 0),
                "output": token_usage.get("output_tokens", 0),
            }

        metadata = None
        if (
            snippets_data
            or web_sources_data
            or tool_calls_data
            or artifacts_data
            or run_step.retrieval_trace
            or run_step.context_trace
        ):
            metadata = {
                "snippets": snippets_data,
                "webSearchSources": web_sources_data,
                "toolCalls": tool_calls_data,
                "artifacts": artifacts_data,
                "retrievalTrace": run_step.retrieval_trace,
                "contextTrace": run_step.context_trace,
            }

        await emitter.step_completed(node_id, response, "completed", tokens, metadata)

    async def _handle_failure(
        self,
        context: NodeExecutionContext,
        node: ExecutionNode,
        exception: Exception,
    ) -> None:
        """Update the run step to FAILED status after an error."""
        try:
            run_step = await self._get_or_create_workflow_run_step(context.workflow_run, node)
            error_msg = f"{type(exception).__name__}: {exception}"
            await self._update_step_status(
                run_step,
                WorkflowRunStepStatus.FAILED,
                error=error_msg,
            )
        except Exception as update_error:
            logger.error(f"Failed to update step {node.id} status on failure: {update_error}")

    # ==================== Config Helpers ====================

    async def _get_step_execution_config(
        self,
        step_data: StepNodeData,
        context: NodeExecutionContext,
    ) -> Dict[str, Any]:
        """Batch DB queries for step execution configuration."""
        def _get_config():
            workflow = context.workflow_run.workflow
            config = {
                'user': workflow.user,
                'content_file_ids': list(step_data.content_files.values_list('id', flat=True)),
                'embedding_file_ids': list(step_data.embedding_files.values_list('id', flat=True)),
                'tag_ids': list(step_data.tags.values_list('id', flat=True)),
                'library_ids': list(step_data.libraries.values_list('id', flat=True)),
                'mcp_server_ids': list(step_data.mcp_servers.values_list('id', flat=True)),
                'prompt_id': step_data.prompt.id if step_data.prompt else None,
                'enable_web_search': step_data.enable_web_search,
                'enable_web_fetch': step_data.enable_web_fetch,
                'enable_artifacts': step_data.enable_artifacts,
                'rag_mode': step_data.rag_mode,
            }
            if context.batch_file_id and context.is_start_connected:
                content_file_ids = config['content_file_ids']
                if context.batch_file_id not in content_file_ids:
                    config['content_file_ids'] = [context.batch_file_id] + content_file_ids
            return config

        return await database_sync_to_async(_get_config)()

    async def _get_llm_for_step(self, step_data: StepNodeData) -> LLM:
        """Get the configured LLM, falling back to default provider."""
        llm = await database_sync_to_async(lambda: step_data.llm)()
        if llm:
            return llm

        logger.warning(f"No LLM configured for step, falling back to {LLMDefaults.DEFAULT_PROVIDER}")
        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(provider=LLMDefaults.DEFAULT_PROVIDER).first()
        )()

        if not default_llm:
            raise ValueError(
                f"No LLM configured and no {LLMDefaults.DEFAULT_PROVIDER} LLM available"
            )

        return default_llm
