"""
Semantic Context Helpers Module

Async functions for retrieving and adding semantic document context
to LLM message arrays via vector similarity search.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from asgiref.sync import sync_to_async

from conversations.constants import RagMode
from core.services.document_processor import DocumentProcessor
from core.services.rag import (ContextAssembler, RetrievalRequest,
                               RetrievedChunk, build_pipeline)
from core.services.vector_service import get_vector_service_async
from libraries.services.library_search import search_libraries

from .db_helpers import (get_files_from_folders, get_files_from_tags,
                         save_document_snippet, save_library_snippet,
                         save_retrieval_trace)

logger = logging.getLogger(__name__)


async def collect_embedding_file_ids(
    embedding_ids: Optional[List[int]],
    tag_ids: Optional[List[int]],
    folder_ids: Optional[List[int]],
    user_id: Optional[int],
) -> Set[int]:
    """
    Collect all file IDs for embedding search from various sources.

    Aggregates file IDs from:
    - Direct embedding_ids
    - Files associated with tag_ids
    - Files in folder_ids

    Args:
        embedding_ids: Direct file IDs to include
        tag_ids: Tag IDs to fetch files from
        folder_ids: Folder IDs to fetch files from
        user_id: User ID for filtering

    Returns:
        Set of file IDs to search for embeddings
    """
    all_file_ids = set(embedding_ids or [])

    if tag_ids:
        tagged_file_ids = await get_files_from_tags(tag_ids, user_id)
        all_file_ids.update(tagged_file_ids)

    if folder_ids:
        folder_file_ids = await get_files_from_folders(folder_ids, user_id)
        all_file_ids.update(folder_file_ids)

    return all_file_ids


async def add_semantic_context_to_messages(
    document_processor: DocumentProcessor,
    messages: List[Dict[str, str]],
    query: str,
    embedding_ids: Optional[List[int]],
    tag_ids: Optional[List[int]],
    folder_ids: Optional[List[int]],
    library_ids: Optional[List[int]],
    user_id: Optional[int],
    file_owner_id: Optional[int],
    is_socratic_mode: bool,
    similarity_threshold: float,
    max_context_snippets: int,
    rag_mode: str = RagMode.ADVANCED,
    message_obj: Optional[Any] = None,
    workflow_run_step_obj: Optional[Any] = None,
) -> None:
    """
    Add semantic search results to messages array.

    Performs vector similarity search on documents and appends
    relevant context to the messages list.

    Args:
        document_processor: DocumentProcessor instance for vector search
        messages: Messages list to append to (modified in place)
        query: User's message to search against
        embedding_ids: Direct file IDs
        tag_ids: Tag IDs for file lookup
        folder_ids: Folder IDs for file lookup
        user_id: Current user ID
        file_owner_id: File owner ID for shared boards
        is_socratic_mode: Whether Socratic mode is enabled
        similarity_threshold: Base similarity threshold
        max_context_snippets: Max number of snippets to retrieve
        rag_mode: Retrieval mode for shared library context
        message_obj: Optional message for snippet tracking
        workflow_run_step_obj: Optional workflow step for snippet tracking
    """
    if not (embedding_ids or tag_ids or folder_ids or library_ids):
        return

    # Agentic mode: retrieval happens on demand through the search_documents
    # tool — nothing is pre-injected, and the tool executor owns the trace
    # lifecycle for the turn.
    if rag_mode == RagMode.AGENTIC:
        return

    # Fresh turn: documents and libraries each save their own trace below, and
    # save_retrieval_trace appends to whatever is on the message — so clear any
    # trace left from a previous generation of this message first.
    if message_obj is not None:
        message_obj.retrieval_trace = None

    effective_threshold = 0.05 if is_socratic_mode else similarity_threshold

    # --- The user's own documents (embeddings / tags / folders) ---
    all_embedding_file_ids = await collect_embedding_file_ids(
        embedding_ids, tag_ids, folder_ids, user_id
    )
    if all_embedding_file_ids:
        # Use file_owner_id for shared boards/conversations, fallback to current user
        vector_user_id = file_owner_id or user_id

        # Initialize vector service if user context changed
        if vector_user_id and vector_user_id != document_processor.user_id:
            document_processor.user_id = vector_user_id
            document_processor.vector_service = await get_vector_service_async(
                vector_user_id
            )

        # Advanced mode routes chat retrieval through the full RAG pipeline
        # (query analysis -> hybrid -> rerank -> grounding -> trace) — the same
        # treatment shared libraries get. Naive mode and workflow steps keep the
        # plain hybrid search.
        if rag_mode == RagMode.ADVANCED and workflow_run_step_obj is None:
            blocks = await _search_documents_for_query(
                document_processor,
                query,
                sorted(all_embedding_file_ids),
                vector_user_id,
                max_context_snippets,
                effective_threshold,
                message_obj,
            )
            context = "\n\n".join(blocks)
        else:
            context = await document_processor.search_similar_documents(
                query_text=query,
                file_ids=list(all_embedding_file_ids),
                user_id=vector_user_id,
                top_k=max_context_snippets,
                similarity_threshold=effective_threshold,
                message_obj=message_obj,
                workflow_run_step_obj=workflow_run_step_obj,
            )

        if context and context.strip():
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Relevant context from documents. When you use a passage, "
                        "cite it inline with its [S#] tag:\n"
                        f"{context}"
                    ),
                }
            )

    # --- Shared libraries (dedicated, un-scoped corpora; no user filter) ---
    if library_ids:
        library_snippets = await _search_libraries_for_query(
            document_processor,
            query,
            library_ids,
            max_context_snippets,
            rag_mode,
            message_obj,
        )
        if library_snippets:
            joined = "\n\n".join(library_snippets)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Relevant context from shared libraries. When you use a "
                        "passage, cite it inline with its [S#] tag:\n"
                        f"{joined}"
                    ),
                }
            )


def run_library_search(
    document_processor: DocumentProcessor,
    query: str,
    library_ids: List[int],
    max_context_snippets: int,
    message_obj: Optional[Any],
) -> List[str]:
    """Thin entry: delegate to the RAG pipeline; persist library citation snippets.

    All heavy lifting (query analysis, hybrid retrieve, rerank, conditional MMR,
    grounding, [S#] assembly) lives in ``core.services.rag``.
    """
    request = RetrievalRequest(
        query=query,
        top_k=max_context_snippets,
        library_ids=tuple(library_ids),
        trace=True,
    )
    pipeline = build_pipeline("library", document_processor.openai_client)

    def _persist(_position, chunk) -> None:
        if message_obj:
            save_library_snippet(message_obj, chunk)

    result = pipeline.run(request, on_keep=_persist)
    if message_obj and result.trace:
        payload = result.trace.to_payload()
        payload["source"] = "libraries"
        save_retrieval_trace(message_obj, payload)
    return result.blocks


def run_document_search(
    document_processor: DocumentProcessor,
    query: str,
    file_ids: List[int],
    user_id: Optional[int],
    max_context_snippets: int,
    similarity_threshold: float,
    message_obj: Optional[Any],
) -> List[str]:
    """Advanced-mode document retrieval: the library pipeline, pointed at files.

    Same stages, same trace; only the retriever differs. The similarity
    threshold keeps its legacy meaning (filter on the hybrid retrieval score,
    before reranking).
    """
    request = RetrievalRequest(
        query=query,
        top_k=max_context_snippets,
        file_ids=tuple(file_ids),
        user_id=user_id,
        similarity_threshold=similarity_threshold,
        trace=True,
    )
    pipeline = build_pipeline("document", document_processor.openai_client)

    def _persist(_position, chunk) -> None:
        if message_obj:
            save_document_snippet(message_obj, chunk)

    result = pipeline.run(request, on_keep=_persist)
    if message_obj and result.trace:
        payload = result.trace.to_payload()
        payload["source"] = "documents"
        save_retrieval_trace(message_obj, payload)
    return result.blocks


async def _search_documents_for_query(
    document_processor: DocumentProcessor,
    query: str,
    file_ids: List[int],
    user_id: Optional[int],
    max_context_snippets: int,
    similarity_threshold: float,
    message_obj: Optional[Any],
) -> List[str]:
    """Async wrapper — document search touches the ORM and opens vector clients."""
    try:
        return await sync_to_async(run_document_search)(
            document_processor,
            query,
            file_ids,
            user_id,
            max_context_snippets,
            similarity_threshold,
            message_obj,
        )
    except Exception as exc:
        logger.warning("Document context retrieval failed: %s", exc)
        return []


def _run_naive_library_search(
    document_processor: DocumentProcessor,
    query: str,
    library_ids: List[int],
    max_context_snippets: int,
    message_obj: Optional[Any],
) -> List[str]:
    """Dense-only shared-library lookup for baseline RAG mode."""
    query_vector = document_processor.openai_client.create_embeddings(query)
    matches = search_libraries(
        query_vector,
        library_ids,
        top_k=max_context_snippets,
        query_text="",
        include_vector=False,
    )
    chunks = [
        RetrievedChunk(
            text=match["text"],
            source_ref=match["source_ref"],
            score=match["score"],
            chunk_index=match.get("chunk_index", 0),
            source_type="library",
            library=match.get("library"),
        )
        for match in matches
    ]

    def _persist(_position, chunk) -> None:
        if message_obj:
            save_library_snippet(message_obj, chunk)

    return ContextAssembler().assemble(chunks, on_keep=_persist)


async def _search_libraries_for_query(
    document_processor: DocumentProcessor,
    query: str,
    library_ids: List[int],
    max_context_snippets: int,
    rag_mode: str,
    message_obj: Optional[Any],
) -> List[str]:
    """Async wrapper — library search touches the ORM and opens vector clients."""
    try:
        if rag_mode == RagMode.NAIVE:
            return await sync_to_async(_run_naive_library_search)(
                document_processor,
                query,
                library_ids,
                max_context_snippets,
                message_obj,
            )
        return await sync_to_async(run_library_search)(
            document_processor, query, library_ids, max_context_snippets, message_obj
        )
    except Exception as exc:
        logger.warning("Library context retrieval failed: %s", exc)
        return []
