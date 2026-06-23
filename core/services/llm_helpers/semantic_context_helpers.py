"""
Semantic Context Helpers Module

Async functions for retrieving and adding semantic document context
to LLM message arrays via vector similarity search.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from asgiref.sync import sync_to_async

from core.services.document_processor import DocumentProcessor
from core.services.vector_service import get_vector_service_async

from .db_helpers import get_files_from_folders, get_files_from_tags

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
        message_obj: Optional message for snippet tracking
        workflow_run_step_obj: Optional workflow step for snippet tracking
    """
    if not (embedding_ids or tag_ids or folder_ids or library_ids):
        return

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
            document_processor, query, library_ids, max_context_snippets, message_obj
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


def _run_library_search(
    document_processor: DocumentProcessor,
    query: str,
    library_ids: List[int],
    max_context_snippets: int,
    message_obj: Optional[Any],
) -> List[str]:
    """Run the Track-A library pipeline and return formatted, cited context.

    query analysis (intent) -> hybrid retrieve -> rerank -> conditional MMR
    -> answer-grounding -> token-budgeted, [S#]-cited context. Every advanced
    stage is opt-in and degrades safely to plain hybrid retrieval.
    """
    from django.conf import settings

    from conversations.models import Snippet
    from core.services import query_analysis_service, rag_postprocess, reranker_service
    from libraries.services.library_search import search_libraries

    # 1) Understand the query (optional). intent gates conditional MMR; the
    #    rewritten/HyDE text feeds retrieval only when HyDE is explicitly enabled.
    plan = query_analysis_service.analyze_query(query)
    exploratory = bool(plan) and plan.get("intent") == "exploratory"

    dense_text, bm25_text = query, query
    if plan and query_analysis_service.use_hyde():
        dense_text = plan.get("hyde_passage") or query
        bm25_text = plan.get("rewritten_query") or query

    query_embedding = document_processor.openai_client.create_embeddings(dense_text)

    # 2) Retrieve a wider pool when we will rerank or diversify it down.
    want_mmr = exploratory  # diversify only broad questions; never precise lookups
    rerank_on = reranker_service.is_enabled()
    multiplier = 5 if rerank_on else (4 if want_mmr else 1)
    pool_k = max_context_snippets * multiplier
    results = search_libraries(
        query_embedding,
        library_ids,
        top_k=pool_k,
        query_text=bm25_text,
        include_vector=want_mmr,  # MMR needs candidate embeddings
    )

    # 3) Rerank for true relevance (keep a wider set if MMR will trim it further).
    if rerank_on:
        working_k = max_context_snippets * 2 if want_mmr else max_context_snippets
        results = reranker_service.rerank(query, results, top_k=working_k)

    # 4) Conditional MMR — diversity for exploratory queries only.
    if want_mmr:
        results = rag_postprocess.mmr_diversify(
            query_embedding, results, top_k=max_context_snippets
        )
    else:
        results = results[:max_context_snippets]

    # 5) Answer-grounding: only trust the flag when the calibrated reranker ran.
    context_parts: List[str] = []
    if rerank_on:
        grounding = rag_postprocess.answer_grounding(results)
        if not grounding["answer_found"]:
            context_parts.append(
                "[grounding] Retrieval confidence is low "
                f"(top score {grounding['top_score']:.2f}). If the passages below "
                "do not answer the question, say it is not in the sources."
            )

    # 6) Assemble: per-snippet cap + total budget + [S#] inline citations.
    char_budget = int(getattr(settings, "RAG_CONTEXT_CHAR_BUDGET", 16000))
    snippet_cap = int(getattr(settings, "RAG_SNIPPET_CHAR_CAP", 2000))
    used = 0
    for idx, result in enumerate(results, 1):
        library = result["library"]
        text = result["text"]
        if len(text) > snippet_cap:
            text = text[:snippet_cap].rstrip() + " …"
        block = (
            f"[S{idx}] {library.name} - {result['source_ref']} (shared library):\n"
            f"{text}"
        )
        if used + len(block) > char_budget and context_parts:
            break  # stay within the prompt budget
        used += len(block)
        context_parts.append(block)
        if message_obj:
            try:
                Snippet.active_objects.create(
                    message=message_obj,
                    file=None,
                    library=library,
                    source_ref=result["source_ref"],
                    text=result["text"],
                    similarity_score=result["score"],
                    chunk_index=result["chunk_index"],
                )
            except Exception as exc:
                logger.warning("Failed to save library snippet: %s", exc)

    return context_parts


async def _search_libraries_for_query(
    document_processor: DocumentProcessor,
    query: str,
    library_ids: List[int],
    max_context_snippets: int,
    message_obj: Optional[Any],
) -> List[str]:
    """Async wrapper — library search touches the ORM and opens vector clients."""
    try:
        return await sync_to_async(_run_library_search)(
            document_processor, query, library_ids, max_context_snippets, message_obj
        )
    except Exception as exc:
        logger.warning("Library context retrieval failed: %s", exc)
        return []
