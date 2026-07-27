"""
Retrieval Tool Executor (agentic RAG).

Executes the ``search_documents`` DARE tool: on-demand retrieval over the
conversation's attached documents and shared libraries through the Advanced
RAG pipeline. The retrieval scope comes from the request DTO, never from the
model's arguments — the model chooses *when* to search, not *what* it is
allowed to see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from asgiref.sync import sync_to_async

from core.services.document_processor import DocumentProcessor
from core.services.llm_helpers.semantic_context_helpers import (
    collect_embedding_file_ids, run_document_search, run_library_search)

logger = logging.getLogger(__name__)

MAX_SEARCH_TOP_K = 10


@dataclass(frozen=True)
class RetrievalScope:
    """Retrieval targets for one chat turn, taken from the request DTO."""

    embedding_ids: Tuple[int, ...] = ()
    tag_ids: Tuple[int, ...] = ()
    folder_ids: Tuple[int, ...] = ()
    library_ids: Tuple[int, ...] = ()
    user_id: Optional[int] = None
    file_owner_id: Optional[int] = None
    max_context_snippets: int = 4
    similarity_threshold: float = 0.5

    def has_sources(self) -> bool:
        return bool(
            self.embedding_ids or self.tag_ids or self.folder_ids or self.library_ids
        )


class RetrievalToolExecutor:
    """Runs the RAG pipeline on demand and persists snippets/trace on the message."""

    async def execute(
        self,
        arguments: Dict[str, Any],
        message: Optional[Any],
        scope: Optional[RetrievalScope],
    ) -> Dict[str, Any]:
        if scope is None or not scope.has_sources():
            return {
                "success": False,
                "error": "No documents or libraries are attached to this conversation.",
            }

        query = (arguments.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "A non-empty search query is required."}

        try:
            top_k = int(arguments.get("top_k") or scope.max_context_snippets)
        except (TypeError, ValueError):
            top_k = scope.max_context_snippets
        top_k = max(1, min(top_k, MAX_SEARCH_TOP_K))

        # save_retrieval_trace appends, so a trace left from a previous
        # generation of this message must be cleared once per turn; multiple
        # search calls within the same turn keep appending.
        if message is not None and not getattr(message, "_agentic_trace_reset", False):
            message.retrieval_trace = None
            message._agentic_trace_reset = True

        document_processor = DocumentProcessor()
        blocks = []

        if scope.embedding_ids or scope.tag_ids or scope.folder_ids:
            file_ids = await collect_embedding_file_ids(
                list(scope.embedding_ids),
                list(scope.tag_ids),
                list(scope.folder_ids),
                scope.user_id,
            )
            if file_ids:
                try:
                    blocks.extend(
                        await sync_to_async(run_document_search)(
                            document_processor,
                            query,
                            sorted(file_ids),
                            scope.file_owner_id or scope.user_id,
                            top_k,
                            scope.similarity_threshold,
                            message,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "search_documents: document retrieval failed: %s", exc
                    )

        if scope.library_ids:
            try:
                blocks.extend(
                    await sync_to_async(run_library_search)(
                        document_processor,
                        query,
                        list(scope.library_ids),
                        top_k,
                        message,
                    )
                )
            except Exception as exc:
                logger.warning("search_documents: library retrieval failed: %s", exc)

        return {
            "success": True,
            "query": query,
            "passages_found": len(blocks),
            "blocks": blocks,
        }


# Global executor instance
retrieval_tool_executor = RetrievalToolExecutor()
