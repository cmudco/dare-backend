"""
Standard Message Helpers Module

Async functions for building standard (non-Socratic) LLM message arrays.
"""

import logging
from typing import List, Dict

from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from core.services.dtos import LLMQueryRequest
from .db_helpers import (
    get_prompt,
    get_conversation_history,
    get_full_file_contents,
    get_referenced_conversations_context,
)
from .semantic_context_helpers import add_semantic_context_to_messages


logger = logging.getLogger(__name__)


async def build_standard_messages(
    request: LLMQueryRequest,
    document_processor: DocumentProcessor,
    file_processor: FileProcessor,
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
        request: LLMQueryRequest containing all query parameters
        document_processor: DocumentProcessor for vector search
        file_processor: FileProcessor for reading file contents

    Returns:
        List of message dictionaries for LLM
    """
    # Extract commonly used values from request
    user_id = request.user.id if request.user else None

    messages = []

    # Add prompt if provided
    prompt = await get_prompt(request.generation.prompt_id)
    if prompt and prompt.strip():
        messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

    # Add referenced conversation context
    if request.context.referenced_conversation_ids:
        referenced_context = await get_referenced_conversations_context(
            request.context.referenced_conversation_ids,
            user_id,
            None
        )
        if referenced_context:
            messages.append({"role": "user", "content": referenced_context})

    # Add full file contents
    if request.context.file_ids:
        file_contents = await get_full_file_contents(request.context.file_ids, file_processor)
        if file_contents:
            for file_content in file_contents:
                messages.append({"role": "user", "content": file_content})

    # Add semantic context from vector search
    await add_semantic_context_to_messages(
        document_processor=document_processor,
        messages=messages,
        query=request.message,
        embedding_ids=request.context.embedding_ids,
        tag_ids=request.context.tag_ids,
        folder_ids=request.context.folder_ids,
        user_id=user_id,
        file_owner_id=request.context.file_owner_id,
        is_socratic_mode=request.is_socratic_mode(),
        similarity_threshold=request.context.document_similarity_threshold,
        max_context_snippets=request.context.max_context_snippets,
        message_obj=request.message_obj,
        workflow_run_step_obj=request.workflow_run_step_obj,
    )

    # Add conversation history
    conversation_history = await get_conversation_history(
        request.conversation,
        limit=request.context.history_limit
    ) if request.conversation else []
    messages.extend([msg for msg in conversation_history if msg["content"].strip()])

    # Add current user message
    messages.append({"role": "user", "content": f"User's message: {request.message}"})

    return messages
