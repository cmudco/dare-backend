"""
Socratic Message Builders

Complete message construction for SocraticBooks classic and advanced modes.
These functions handle all logging, history retrieval, vector service init,
document context retrieval, and prompt assembly.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from conversations.constants import RagMode
from core.services.document_processor import DocumentProcessor
from core.services.dtos import LLMQueryRequest

from .context_trace import ContextTraceRecorder
from .history_helpers import get_conversation_history
from .semantic_context_helpers import add_semantic_context_to_messages

logger = logging.getLogger(__name__)


@dataclass
class SocraticBuildResult:
    """Messages plus the context-assembly trace for a socratic turn."""

    messages: List[Dict[str, str]] = field(default_factory=list)
    context_trace: Optional[Dict[str, Any]] = None


# Under agentic RAG the model retrieves on demand via the search_documents
# tool, so the builders skip their similarity-search pre-injection and hand
# the model this directive instead.
AGENTIC_RETRIEVAL_DIRECTIVE = (
    "Document context is not pre-loaded. Call the search_documents tool to "
    "retrieve relevant passages from the attached course materials before "
    "answering questions about their content."
)


# ============================================================================
# Public API - These are the only exports
# ============================================================================


async def build_classic_socratic_messages(
    request: LLMQueryRequest,
    document_processor: DocumentProcessor,
) -> SocraticBuildResult:
    """
    Build complete message array for classic SocraticBooks mode.

    The classic format establishes the AI as a "living Socratic book" that helps
    students learn through dialogue. System prompt defines the teaching context,
    user message includes document context and conversation history.

    Args:
        request: LLMQueryRequest containing all query parameters
        document_processor: DocumentProcessor for vector similarity search

    Returns:
        SocraticBuildResult with [system_message, user_message] and the
        context-assembly trace.
    """
    # Extract Socratic metadata from request
    subject = request.socratic.get_subject()
    topic = request.socratic.get_topic()
    learning_goals = request.socratic.get_learning_goals()
    chat_prompt = request.socratic.get_chat_prompt()

    _log_socratic_components(
        mode="classic",
        subject=subject,
        topic=topic,
        chat_prompt=chat_prompt,
        learning_goals=learning_goals,
    )

    trace = ContextTraceRecorder()

    with trace.stage("prompt") as stage:
        system_prompt = _build_classic_system_prompt(
            subject=subject,
            topic=topic,
            chat_prompt=chat_prompt,
            learning_goals=learning_goals,
        )
        stage["chars"] = len(system_prompt)

    with trace.stage("history") as stage:
        history_list = (
            await get_conversation_history(
                request.conversation, limit=request.context.history_limit
            )
            if request.conversation
            else []
        )
        conversation_history = _format_transcript(history_list)
        if history_list:
            stage["turns"] = len(history_list)
            stage["limit"] = request.context.history_limit

    with trace.stage("retrieval") as stage:
        if request.context.rag_mode == RagMode.AGENTIC:
            file_context = AGENTIC_RETRIEVAL_DIRECTIVE
            stage["mode"] = RagMode.AGENTIC
            stage["deferredToTool"] = True
        elif request.context.embedding_ids:
            doc_context = await _retrieve_document_context(request, document_processor)

            file_context = _format_document_snippets(
                doc_context, fallback="No relevant file content found."
            )
            stage["mode"] = request.context.rag_mode
            stage["threshold"] = request.context.document_similarity_threshold
            stage["topK"] = request.context.max_context_snippets
            stage["chars"] = len(doc_context or "")
        else:
            file_context = _format_document_snippets(
                "", fallback="No relevant file content found."
            )

    user_message = _build_classic_user_message(
        document_context=file_context,
        conversation_history=conversation_history,
        question=request.message,
    )

    return SocraticBuildResult(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        context_trace=trace.to_payload(),
    )


async def build_advanced_socratic_messages(
    request: LLMQueryRequest,
    document_processor: DocumentProcessor,
) -> SocraticBuildResult:
    """
    Build complete message array for advanced SocraticBooks mode.

    The advanced format embeds all context (conversation history, documents,
    learning goals) into a comprehensive system prompt. The user message
    is sent as a simple separate turn for chat API compliance.

    Args:
        request: LLMQueryRequest containing all query parameters
        document_processor: DocumentProcessor for vector similarity search

    Returns:
        SocraticBuildResult with [system_message, user_message] and the
        context-assembly trace.
    """
    # Extract Socratic metadata from request
    title = request.socratic.get_title() or (
        request.conversation.title
        if request.conversation and request.conversation.title
        else "Untitled Conversation"
    )
    subject = request.socratic.get_subject()
    topic = request.socratic.get_topic()
    learning_goals = request.socratic.get_learning_goals()
    chat_prompt = request.socratic.get_chat_prompt()

    _log_socratic_components(
        mode="advanced",
        subject=subject,
        topic=topic,
        title=title,
        chat_prompt=chat_prompt,
        learning_goals=learning_goals,
    )

    trace = ContextTraceRecorder()

    with trace.stage("history") as stage:
        history_list = (
            await get_conversation_history(
                request.conversation, limit=request.context.history_limit
            )
            if request.conversation
            else []
        )
        conversation_history = _format_transcript(history_list)
        if history_list:
            stage["turns"] = len(history_list)
            stage["limit"] = request.context.history_limit

    with trace.stage("retrieval") as stage:
        if request.context.rag_mode == RagMode.AGENTIC:
            relevant_content = AGENTIC_RETRIEVAL_DIRECTIVE
            stage["mode"] = RagMode.AGENTIC
            stage["deferredToTool"] = True
        elif request.context.embedding_ids:
            doc_context = await _retrieve_document_context(request, document_processor)

            relevant_content = _format_document_snippets(
                doc_context, fallback="No relevant external content found."
            )
            stage["mode"] = request.context.rag_mode
            stage["threshold"] = request.context.document_similarity_threshold
            stage["topK"] = request.context.max_context_snippets
            stage["chars"] = len(doc_context or "")
        else:
            relevant_content = _format_document_snippets(
                "", fallback="No relevant external content found."
            )

    with trace.stage("prompt") as stage:
        system_prompt = _build_advanced_system_prompt(
            title=title,
            subject=subject,
            topic=topic,
            learning_goals=learning_goals,
            chat_prompt=chat_prompt,
            conversation_history=conversation_history,
            relevant_content=relevant_content,
            user_message=request.message,
        )
        stage["chars"] = len(system_prompt)

    return SocraticBuildResult(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.message},
        ],
        context_trace=trace.to_payload(),
    )


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

    return (
        "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."
    )


def _format_document_snippets(raw_context: str, fallback: str) -> str:
    """Format raw document context into clean snippets."""
    if not raw_context:
        return fallback

    parts = [p for p in raw_context.split("\n\n") if p and p.strip()]
    return "\n\n".join(parts) if parts else fallback


async def _retrieve_document_context(
    request: LLMQueryRequest,
    document_processor: DocumentProcessor,
) -> str:
    """Use DARE's retrieval pipeline without changing the Socratic teaching prompt."""
    context_messages = []
    failures = await add_semantic_context_to_messages(
        document_processor=document_processor,
        messages=context_messages,
        query=request.message,
        embedding_ids=request.context.embedding_ids,
        tag_ids=request.context.tag_ids,
        folder_ids=request.context.folder_ids,
        library_ids=request.context.library_ids,
        user_id=request.user.id if request.user else None,
        payer_bot_id=(
            getattr(request.conversation, "bot_id", None)
            if request.user is None
            else None
        ),
        file_owner_id=request.context.file_owner_id,
        is_socratic_mode=True,
        similarity_threshold=request.context.document_similarity_threshold,
        max_context_snippets=request.context.max_context_snippets,
        rag_mode=request.context.rag_mode,
        message_obj=request.message_obj,
        workflow_run_step_obj=request.workflow_run_step_obj,
    )
    parts = [message["content"] for message in context_messages]
    if failures:
        parts.append(
            "Some document retrieval failed. Do not claim the unavailable sources were searched."
        )
    return "\n\n".join(parts)


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
        + "\n\nTeaching Style:\n"
        + chat_prompt
        + "\n\nLearning Goals:\n"
        + learning_goals
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
        f'The latest user message was: "{user_message}"\n\n'
        f"Please respond according to these directions:\n{chat_prompt}"
    )
