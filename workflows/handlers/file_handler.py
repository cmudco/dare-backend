"""
File node handler for workflow execution.

Retrieves file content via vector search (embeddings) or full content extraction.
Does NOT perform LLM calls — purely retrieval-focused.
"""
import logging
from typing import Optional, List

from django.utils import timezone
from channels.db import database_sync_to_async

from workflows.constants import RetrievalMode, QuerySource
from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.utils import NodeType
from workflows.models import FileNodeData
from conversations.services.websocket_response_service import WebSocketResponseService

from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from core.services.vector_service import get_vector_service_async
from files.models import File


logger = logging.getLogger(__name__)

# Max chars sent to FE via WebSocket — full output preserved for downstream nodes
WS_RESPONSE_PREVIEW_LIMIT = 500


class FileNodeHandler(BaseNodeHandler):
    """
    Handler for 'file' type nodes.

    Retrieves file content via two modes:
    - Embeddings: Vector search using query from previous step or text input
    - Content: Full file content extraction
    - Both: Combined output

    Does NOT perform LLM calls — purely retrieval.
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'file' nodes."""
        return node_type == NodeType.FILE

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a file node by retrieving file content.

        Args:
            node: The file node to execute
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with retrieved content
        """
        start_time = timezone.now()
        correlation_id = f"file-{node.id}"

        try:
            # Get and validate node data
            file_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not file_data or not isinstance(file_data, FileNodeData):
                logger.error(f"[{correlation_id}] Invalid or missing file node data")
                return NodeExecutionResult(
                    success=False,
                    error="Invalid or missing file node data"
                )

            logger.info(
                f"[{correlation_id}] Starting file node execution, "
                f"mode={file_data.retrieval_mode}"
            )

            # Send step_started event so FE initializes streaming state
            if context.send_callback:
                try:
                    await context.send_callback(
                        WebSocketResponseService.format_workflow_step_started(
                            node_id=node.id,
                            step_number=file_data.step_number or 0,
                            node_type="file"
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to send step_started event: {e}")

            # Get active files
            files = await self._get_files(file_data)
            if not files:
                return NodeExecutionResult(
                    success=True,
                    output="No files configured for retrieval.",
                    metadata={'file_count': 0, 'retrieval_mode': file_data.retrieval_mode}
                )

            # Execute based on retrieval mode
            if file_data.retrieval_mode == RetrievalMode.EMBEDDINGS:
                output = await self._retrieve_embeddings(file_data, files, context)
            elif file_data.retrieval_mode == RetrievalMode.CONTENT:
                output = await self._retrieve_full_content(file_data, files)
            else:  # RetrievalMode.BOTH
                embeddings_output = await self._retrieve_embeddings(
                    file_data, files, context
                )
                content_output = await self._retrieve_full_content(file_data, files)
                output = (
                    f"=== Relevant Snippets ===\n{embeddings_output}"
                    f"\n\n=== Full Content ===\n{content_output}"
                )

            execution_time = (timezone.now() - start_time).total_seconds()
            logger.info(
                f"[{correlation_id}] File retrieval completed in {execution_time:.2f}s"
            )

            metadata = {
                'retrieval_mode': file_data.retrieval_mode,
                'file_count': len(files),
                'file_ids': [f.id for f in files],
            }

            # Send truncated preview to FE — full output stays in result for downstream nodes
            await self._send_completion(
                context, node.id, output, metadata
            )

            return NodeExecutionResult(
                success=True,
                output=output,
                execution_time=execution_time,
                metadata=metadata,
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"

            logger.error(
                f"[{correlation_id}] File node execution failed "
                f"({error_type}): {str(e)}",
                exc_info=True
            )

            execution_time = (timezone.now() - start_time).total_seconds()
            return NodeExecutionResult(
                success=False,
                error=error_msg,
                execution_time=execution_time
            )

    # ============================================================================
    # WebSocket Helpers
    # ============================================================================

    @staticmethod
    def _build_ws_response(output: str) -> str:
        """
        Build a truncated preview for WebSocket transmission.

        Full content is preserved in NodeExecutionResult.output for downstream
        nodes. The FE only needs a summary to display node status.
        """
        if len(output) <= WS_RESPONSE_PREVIEW_LIMIT:
            return output
        return output[:WS_RESPONSE_PREVIEW_LIMIT] + f"... ({len(output)} chars total)"

    async def _send_completion(
        self,
        context: NodeExecutionContext,
        node_id: str,
        output: str,
        metadata: dict,
    ) -> None:
        """Send truncated completion message to FE via WebSocket."""
        if not context.send_callback:
            return

        try:
            await context.send_callback(
                WebSocketResponseService.format_workflow_step_completed(
                    node_id=node_id,
                    response=self._build_ws_response(output),
                    status="completed",
                    metadata=metadata,
                )
            )
        except Exception as e:
            logger.debug(f"WebSocket callback failed for file node {node_id}: {e}")

    # ============================================================================
    # Private Helpers
    # ============================================================================

    async def _get_files(self, file_data: FileNodeData) -> List[File]:
        """Get active, non-deleted files from node data."""
        return await database_sync_to_async(
            lambda: list(file_data.files.filter(is_deleted=False, is_active=True))
        )()

    def _get_query_text(
        self,
        file_data: FileNodeData,
        previous_results: dict
    ) -> str:
        """
        Resolve query text based on query_source configuration.

        Args:
            file_data: File node configuration
            previous_results: Results from previously executed nodes

        Returns:
            Query text for vector search
        """
        if file_data.query_source == QuerySource.TEXT_INPUT:
            return file_data.text_input or ""

        # QuerySource.PREVIOUS_STEP — use first available previous output
        if previous_results:
            for node_id, result in previous_results.items():
                output = result.get('output', '')
                if output:
                    return output

        # Fallback to text_input if no previous results available
        return file_data.text_input or ""

    async def _retrieve_embeddings(
        self,
        file_data: FileNodeData,
        files: List[File],
        context: NodeExecutionContext
    ) -> str:
        """
        Retrieve file content via vector search.

        Uses DocumentProcessor.search_similar_documents() for vector similarity
        search against the configured files.

        Args:
            file_data: File node configuration
            files: List of files to search
            context: Execution context

        Returns:
            Formatted string with retrieved snippets
        """
        query_text = self._get_query_text(file_data, context.previous_results)

        if not query_text:
            return "No query text available for vector search."

        # Get user from workflow
        user = await database_sync_to_async(
            lambda: context.workflow_run.workflow.user
        )()

        # Initialize document processor with user's configured vector service
        # Must use async version to properly check user's vector_db preference (Pinecone/Weaviate)
        vector_service = await get_vector_service_async(user.id)
        document_processor = DocumentProcessor(user_id=user.id, vector_service=vector_service)

        file_ids = [f.id for f in files]

        context_text = await document_processor.search_similar_documents(
            query_text=query_text,
            file_ids=file_ids,
            user_id=user.id,
            top_k=file_data.max_results,
            similarity_threshold=file_data.similarity_threshold,
        )

        if not context_text:
            query_preview = query_text[:100]
            return f"No relevant content found for query: '{query_preview}...'"

        if file_data.include_metadata:
            query_preview = query_text[:200]
            return f"Query: {query_preview}...\n\nRetrieved Content:\n{context_text}"

        return context_text

    async def _retrieve_full_content(
        self,
        file_data: FileNodeData,
        files: List[File]
    ) -> str:
        """
        Retrieve full content from files.

        Uses FileProcessor.read_file_content() for each file.

        Args:
            file_data: File node configuration
            files: List of files to retrieve content from

        Returns:
            Formatted string with full file content
        """
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
