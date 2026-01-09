"""
Socratic Message Builders

Complete message construction for SocraticBooks classic and advanced modes.
These functions handle all logging, history retrieval, vector service init,
document context retrieval, and prompt assembly.
"""

import logging
from typing import List, Dict, Any, Optional

from core.services.dtos import MessageBuildContext
from core.services.document_processor import DocumentProcessor
from core.services.vector_service import get_vector_service_async
from .db_helpers import get_conversation_history


logger = logging.getLogger(__name__)


# ============================================================================
# Public API - These are the only exports
# ============================================================================

async def build_classic_socratic_messages(
    context: MessageBuildContext,
    document_processor: DocumentProcessor,
) -> List[Dict[str, str]]:
    """
    Build complete message array for classic SocraticBooks mode.

    The classic format establishes the AI as a "living Socratic book" that helps
    students learn through dialogue. System prompt defines the teaching context,
    user message includes document context and conversation history.

    Args:
        context: MessageBuildContext with all request data
        document_processor: DocumentProcessor for vector similarity search

    Returns:
        List of message dicts ready for LLM API: [system_message, user_message]
    """
    subject = context.subject
    topic = context.topic
    learning_goals = context.learning_goals
    chat_prompt = context.chat_prompt

    _log_socratic_components(
        mode="classic",
        subject=subject,
        topic=topic,
        chat_prompt=chat_prompt,
        learning_goals=learning_goals,
    )

    system_prompt = _build_classic_system_prompt(
        subject=subject,
        topic=topic,
        chat_prompt=chat_prompt,
        learning_goals=learning_goals,
    )

    history_list = await get_conversation_history(
        context.conversation,
        limit=context.history_limit
    ) if context.conversation else []

    conversation_history = _format_transcript(history_list)

    # Classic mode uses file_owner_id for shared boards (deployed Socratic bots)
    vector_user_id = context.file_owner_id or context.user_id

    doc_context = await _retrieve_document_context(
        document_processor=document_processor,
        query=context.message,
        file_ids=context.embedding_ids,
        user_id=vector_user_id,
        top_k=context.max_context_snippets,
        similarity_threshold=context.document_similarity_threshold,
        message_obj=context.message_obj,
        workflow_run_step_obj=context.workflow_run_step_obj,
    )

    file_context = _format_document_snippets(doc_context, fallback="No relevant file content found.")

    user_message = _build_classic_user_message(
        document_context=file_context,
        conversation_history=conversation_history,
        question=context.message,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


async def build_advanced_socratic_messages(
    context: MessageBuildContext,
    document_processor: DocumentProcessor,
) -> List[Dict[str, str]]:
    """
    Build complete message array for advanced SocraticBooks mode.

    The advanced format embeds all context (conversation history, documents,
    learning goals) into a comprehensive system prompt. The user message
    is sent as a simple separate turn for chat API compliance.

    Args:
        context: MessageBuildContext with all request data
        document_processor: DocumentProcessor for vector similarity search

    Returns:
        List of message dicts ready for LLM API: [system_message, user_message]
    """
    title = context.title or (
        context.conversation.title
        if context.conversation and context.conversation.title
        else "Untitled Conversation"
    )
    subject = context.subject
    topic = context.topic
    learning_goals = context.learning_goals
    chat_prompt = context.chat_prompt

    _log_socratic_components(
        mode="advanced",
        subject=subject,
        topic=topic,
        title=title,
        chat_prompt=chat_prompt,
        learning_goals=learning_goals,
    )

    history_list = await get_conversation_history(
        context.conversation,
        limit=context.history_limit
    ) if context.conversation else []

    conversation_history = _format_transcript(history_list)

    # Advanced mode uses user_id directly (not file_owner_id)
    doc_context = await _retrieve_document_context(
        document_processor=document_processor,
        query=context.message,
        file_ids=context.embedding_ids,
        user_id=context.user_id,
        top_k=context.max_context_snippets,
        similarity_threshold=context.document_similarity_threshold,
        message_obj=context.message_obj,
        workflow_run_step_obj=context.workflow_run_step_obj,
    )

    relevant_content = _format_document_snippets(doc_context, fallback="No relevant external content found.")

    system_prompt = _build_advanced_system_prompt(
        title=title,
        subject=subject,
        topic=topic,
        learning_goals=learning_goals,
        chat_prompt=chat_prompt,
        conversation_history=conversation_history,
        relevant_content=relevant_content,
        user_message=context.message,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context.message},
    ]


# ============================================================================
# Private Helpers - Internal to this module
# ============================================================================

def _log_socratic_components(
    mode: str,
    subject: str,
    topic: str,
    chat_prompt: str,
    learning_goals: str,
    title: Optional[str] = None,
) -> None:
    """Log Socratic message components for debugging."""
    mode_label = "ADVANCED" if mode == "advanced" else "classic"

    if title:
        logger.info(
            f"[LLMService] Building Socratic messages ({mode_label} mode): "
            f"title={title}, subject={subject}, topic={topic}"
        )
    else:
        logger.info(
            f"[LLMService] Building Socratic messages ({mode_label} mode): "
            f"subject={subject}, topic={topic}"
        )

    logger.info(
        f"[LLMService] chat_prompt being attached to system message: "
        f"{chat_prompt[:150] if chat_prompt else 'N/A'}..."
    )
    logger.info(
        f"[LLMService] learning_goals being attached: "
        f"{learning_goals[:100] if learning_goals else 'N/A'}..."
    )


def _format_transcript(history: List[Dict[str, str]]) -> str:
    """Format message history as readable transcript."""
    if not history:
        return "No previous messages."

    transcript_parts = []
    for h in history:
        role_name = "User" if h["role"] == "user" else "Assistant"
        content = (h["content"] or "").strip()
        if content:
            transcript_parts.append(f"{role_name}: {content}")

    return "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."


def _format_document_snippets(raw_context: str, fallback: str) -> str:
    """Format raw document context into clean snippets."""
    if not raw_context:
        return fallback

    parts = [p for p in raw_context.split("\n\n") if p and p.strip()]
    return "\n\n".join(parts) if parts else fallback


async def _retrieve_document_context(
    document_processor: DocumentProcessor,
    query: str,
    file_ids: List[int],
    user_id: Optional[int],
    top_k: int,
    similarity_threshold: float,
    message_obj: Optional[Any] = None,
    workflow_run_step_obj: Optional[Any] = None,
) -> str:
    """Retrieve relevant document snippets via vector similarity search."""
    if not file_ids:
        return ""

    # Initialize vector service if user context changed
    if user_id and user_id != document_processor.user_id:
        document_processor.user_id = user_id
        document_processor.vector_service = await get_vector_service_async(user_id)

    return await document_processor.search_similar_documents(
        query_text=query,
        file_ids=file_ids,
        user_id=user_id,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        message_obj=message_obj,
        workflow_run_step_obj=workflow_run_step_obj,
    )


def _build_classic_system_prompt(
    subject: str,
    topic: str,
    chat_prompt: str,
    learning_goals: str,
) -> str:
    """Build system prompt for classic Socratic mode."""
    prompt_start = (
        f"Subject and Topic:\n"
        f"Your job is to act as a living Socratic book that helps '{subject}' students\n"
        f"learn about different subjects. This chapter specifically is about '{topic}'."
    )
    return (
        prompt_start
        + "\n\nTeaching Style:\n" + chat_prompt
        + "\n\nLearning Goals:\n" + learning_goals
    )


def _build_classic_user_message(
    document_context: str,
    conversation_history: str,
    question: str,
) -> str:
    """Build user message for classic Socratic mode."""
    return (
        "Respond based on the following documents.\n"
        f"{document_context}\n"
        "And the recent conversation history:\n"
        f"{conversation_history}\n"
        f"Question: {question}\n"
    )


def _build_advanced_system_prompt(
    title: str,
    subject: str,
    topic: str,
    learning_goals: str,
    chat_prompt: str,
    conversation_history: str,
    relevant_content: str,
    user_message: str,
) -> str:
    """Build comprehensive system prompt for advanced Socratic mode."""
    return (
        f"Here is a conversation:\n{conversation_history}\n\n"
        f"This is a conversation on {title} (Subject: {subject}, Topic: {topic}).\n"
        f"We are trying to teach the following learning goals:\n{learning_goals}\n\n"
        f"{relevant_content}\n"
        f"The latest user message was: \"{user_message}\"\n\n"
        f"Please respond according to these directions:\n{chat_prompt}"
    )
