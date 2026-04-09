"""
File node handler for workflow execution.

Retrieves file content via vector search (embeddings) or full content extraction.
Does NOT perform LLM calls — purely retrieval-focused.
"""
import logging
from typing import List, Optional

from channels.db import database_sync_to_async
from django.utils import timezone

from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from core.services.vector_service import get_vector_service_async
from files.models import File
from workflows.constants import QuerySource, RetrievalMode
from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.event_emitter import EventEmitter
from workflows.handlers.utils import NodeType
from workflows.models import FileNodeData


logger = logging.getLogger(__name__)

# Max chars sent to FE via WebSocket — full output preserved for downstream nodes
WS_RESPONSE_PREVIEW_LIMIT = 500


class FileNodeHandler(BaseNodeHandler):
    """
    Handler for 'file' type nodes.

    Retrieval modes: Embeddings | Content | Both.
    No LLM calls — purely retrieval.
    """

    def can_handle(self, node_type: str) -> bool:
        return node_type == NodeType.FILE

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """
        Pipeline: validate → start → get files → retrieve → complete
        """
        start_time = timezone.now()
        correlation_id = f"file-{node.id}"
        emitter = EventEmitter(context.send_callback, workflow_run_id=context.workflow_run.id)

        try:
            # Validate
            file_data = await database_sync_to_async(lambda: node.db_node.data_object)()
            if not file_data or not isinstance(file_data, FileNodeData):
                return NodeExecutionResult(success=False, error="Invalid or missing file node data")

            logger.info(f"[{correlation_id}] Starting, mode={file_data.retrieval_mode}")

            # Start
            await emitter.step_started(node.id, node.label, "file", timezone.now())

            # Get files
            files = await self._get_files(file_data, context)
            if not files:
                return NodeExecutionResult(
                    success=True,
                    output="No files configured for retrieval.",
                    metadata={'file_count': 0, 'retrieval_mode': file_data.retrieval_mode},
                )

            # Retrieve
            output = await self._retrieve(file_data, files, context)

            execution_time = (timezone.now() - start_time).total_seconds()
            logger.info(f"[{correlation_id}] Completed in {execution_time:.2f}s")

            metadata = {
                'retrieval_mode': file_data.retrieval_mode,
                'file_count': len(files),
                'file_ids': [f.id for f in files],
            }

            # Complete — send truncated preview to FE
            await emitter.step_completed(
                node.id, self._truncate(output), "completed", metadata=metadata,
            )

            return NodeExecutionResult(
                success=True,
                output=output,
                execution_time=execution_time,
                metadata=metadata,
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {e}"
            logger.error(f"[{correlation_id}] Failed ({error_type}): {e}", exc_info=True)
            execution_time = (timezone.now() - start_time).total_seconds()
            return NodeExecutionResult(success=False, error=error_msg, execution_time=execution_time)

    # ==================== Retrieval ====================

    async def _retrieve(
        self,
        file_data: FileNodeData,
        files: List[File],
        context: NodeExecutionContext,
    ) -> str:
        """Dispatch to the correct retrieval mode."""
        if file_data.retrieval_mode == RetrievalMode.EMBEDDINGS:
            return await self._retrieve_embeddings(file_data, files, context)
        if file_data.retrieval_mode == RetrievalMode.CONTENT:
            return await self._retrieve_full_content(file_data, files)

        # RetrievalMode.BOTH
        embeddings_output = await self._retrieve_embeddings(file_data, files, context)
        content_output = await self._retrieve_full_content(file_data, files)
        return (
            f"=== Relevant Snippets ===\n{embeddings_output}"
            f"\n\n=== Full Content ===\n{content_output}"
        )

    # ==================== Helpers ====================

    @staticmethod
    def _truncate(output: str) -> str:
        """Truncate for WebSocket preview."""
        if len(output) <= WS_RESPONSE_PREVIEW_LIMIT:
            return output
        return output[:WS_RESPONSE_PREVIEW_LIMIT] + f"... ({len(output)} chars total)"

    async def _get_files(
        self, file_data: FileNodeData, context: NodeExecutionContext,
    ) -> List[File]:
        """Get active, non-deleted files with ownership validation."""
        def _get_validated_files():
            workflow = context.workflow_run.workflow
            return list(
                file_data.files.filter(
                    is_deleted=False,
                    is_active=True,
                    user_id=workflow.user_id,
                )
            )
        return await database_sync_to_async(_get_validated_files)()

    def _get_query_text(self, file_data: FileNodeData, previous_results: dict) -> str:
        """Resolve query text based on query_source configuration."""
        if file_data.query_source == QuerySource.TEXT_INPUT:
            return file_data.text_input or ""

        if previous_results:
            for node_id, result in previous_results.items():
                output = result.get('output', '')
                if output:
                    return output

        return file_data.text_input or ""

    async def _retrieve_embeddings(
        self,
        file_data: FileNodeData,
        files: List[File],
        context: NodeExecutionContext,
    ) -> str:
        """Retrieve via vector search."""
        query_text = self._get_query_text(file_data, context.previous_results)
        if not query_text:
            return "No query text available for vector search."

        workflow = await database_sync_to_async(lambda: context.workflow_run.workflow)()
        user = await database_sync_to_async(lambda: workflow.user)()

        vector_service = await get_vector_service_async(user.id)
        document_processor = DocumentProcessor(user_id=user.id, vector_service=vector_service)

        context_text = await document_processor.search_similar_documents(
            query_text=query_text,
            file_ids=[f.id for f in files],
            user_id=user.id,
            top_k=file_data.max_results,
            similarity_threshold=file_data.similarity_threshold,
        )

        if not context_text:
            return f"No relevant content found for query: '{query_text[:100]}...'"

        if file_data.include_metadata:
            return f"Query: {query_text[:200]}...\n\nRetrieved Content:\n{context_text}"

        return context_text

    async def _retrieve_full_content(
        self,
        file_data: FileNodeData,
        files: List[File],
    ) -> str:
        """Retrieve full file content."""
        file_processor = FileProcessor()
        content_parts = []

        for file in files:
            try:
                content = await database_sync_to_async(
                    lambda f=file: file_processor.read_file_content(f)
                )()

                if file_data.include_metadata:
                    file_name = file.name or file.file.name
                    content_parts.append(f"=== {file_name} ===\n{content}")
                else:
                    content_parts.append(content)

            except Exception as e:
                file_name = file.name or file.file.name
                logger.warning(f"Failed to read file {file.id} ({file_name}): {e}")
                content_parts.append(f"[Error reading file: {file_name}]")

        return "\n\n".join(content_parts)
