"""
Standard Message Helpers Module

Async functions for building standard (non-Socratic) LLM message arrays.
"""

import logging
from typing import List, Dict, Optional, Any

from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from .db_helpers import (
    get_prompt,
    get_conversation_history,
    get_full_file_contents,
    get_referenced_conversations_context,
)
from .semantic_context_helpers import add_semantic_context_to_messages


logger = logging.getLogger(__name__)


async def build_standard_messages(
    request_message: str,
    conversation: Optional[Any],
    document_processor: DocumentProcessor,
    file_processor: FileProcessor,
    prompt_id: Optional[str],
    referenced_conversation_ids: Optional[List[str]],
    file_ids: Optional[List[int]],
    embedding_ids: Optional[List[int]],
    tag_ids: Optional[List[int]],
    folder_ids: Optional[List[int]],
    user_id: Optional[int],
    file_owner_id: Optional[int],
    is_socratic_mode: bool,
    similarity_threshold: float,
    max_context_snippets: int,
    history_limit: int,
    message_obj: Optional[Any] = None,
    workflow_run_step_obj: Optional[Any] = None,
) -> List[Dict[str, str]]:
    """
    Build messages for standard (non-Socratic) mode.

    Assembles messages from:
    - System prompt (if provided)
    - Referenced conversation context
    - File contents
    - Semantic document context (via vector search)
    - Conversation history
    - Current user message

    Args:
        request_message: The user's current message
        conversation: Conversation instance (or None)
        document_processor: DocumentProcessor for vector search
        file_processor: FileProcessor for reading file contents
        prompt_id: Optional prompt ID to fetch
        referenced_conversation_ids: IDs of conversations to include as context
        file_ids: File IDs for full content inclusion
        embedding_ids: File IDs for semantic search
        tag_ids: Tag IDs for semantic search file lookup
        folder_ids: Folder IDs for semantic search file lookup
        user_id: Current user ID
        file_owner_id: File owner ID for shared boards
        is_socratic_mode: Whether Socratic mode is enabled
        similarity_threshold: Similarity threshold for vector search
        max_context_snippets: Max snippets from vector search
        history_limit: Max conversation history messages
        message_obj: Optional message for snippet tracking
        workflow_run_step_obj: Optional workflow step for snippet tracking

    Returns:
        List of message dictionaries for LLM
    """
    messages = []

    # Add prompt if provided
    prompt = await get_prompt(prompt_id)
    if prompt and prompt.strip():
        messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

    # Add referenced conversation context
    if referenced_conversation_ids:
        referenced_context = await get_referenced_conversations_context(
            referenced_conversation_ids,
            user_id,
            None
        )
        if referenced_context:
            messages.append({"role": "user", "content": referenced_context})

    # Add full file contents
    if file_ids:
        file_contents = await get_full_file_contents(file_ids, file_processor)
        if file_contents:
            for file_content in file_contents:
                messages.append({"role": "user", "content": file_content})

    # Add semantic context from vector search
    await add_semantic_context_to_messages(
        document_processor=document_processor,
        messages=messages,
        query=request_message,
        embedding_ids=embedding_ids,
        tag_ids=tag_ids,
        folder_ids=folder_ids,
        user_id=user_id,
        file_owner_id=file_owner_id,
        is_socratic_mode=is_socratic_mode,
        similarity_threshold=similarity_threshold,
        max_context_snippets=max_context_snippets,
        message_obj=message_obj,
        workflow_run_step_obj=workflow_run_step_obj,
    )

    # Add conversation history
    conversation_history = await get_conversation_history(
        conversation,
        limit=history_limit
    ) if conversation else []
    messages.extend([msg for msg in conversation_history if msg["content"].strip()])

    # Add current user message
    messages.append({"role": "user", "content": f"User's message: {request_message}"})

    return messages
