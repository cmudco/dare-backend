"""
Semantic Context Helpers Module

Async functions for retrieving and adding semantic document context
to LLM message arrays via vector similarity search.
"""

import logging
from typing import List, Dict, Set, Optional, Any

from core.services.document_processor import DocumentProcessor
from core.services.vector_service import get_vector_service_async
from .db_helpers import get_files_from_tags, get_files_from_folders


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
    if not (embedding_ids or tag_ids or folder_ids):
        return

    all_embedding_file_ids = await collect_embedding_file_ids(
        embedding_ids, tag_ids, folder_ids, user_id
    )
    if not all_embedding_file_ids:
        return

    # Use file_owner_id for shared boards/conversations, fallback to current user
    vector_user_id = file_owner_id or user_id

    # Initialize vector service if user context changed
    if vector_user_id and vector_user_id != document_processor.user_id:
        document_processor.user_id = vector_user_id
        document_processor.vector_service = await get_vector_service_async(vector_user_id)

    effective_threshold = 0.05 if is_socratic_mode else similarity_threshold

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
        messages.append({"role": "user", "content": f"Relevant context from documents:\n{context}"})
