"""
Standard Message Helpers Module

Async functions for building standard (non-Socratic) LLM message arrays.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from channels.db import database_sync_to_async

from conversations.constants import RagMode
from core.services.document_processor import DocumentProcessor
from core.services.dtos import LLMQueryRequest
from core.services.file_processor import FileProcessor

from .context_trace import ContextTraceRecorder
from .db_helpers import (
    get_full_file_contents,
    get_prompt,
    get_referenced_conversations_context,
    get_referenced_summaries_context,
    get_selected_file_names,
)
from .history_helpers import get_conversation_history
from .memory_context_helpers import add_memory_context_to_messages
from .semantic_context_helpers import add_semantic_context_to_messages

logger = logging.getLogger(__name__)


@dataclass
class MessageBuildResult:
    """Result from building LLM messages, including any side-channel data."""

    messages: List[Dict[str, str]] = field(default_factory=list)
    memory_context: List[Dict[str, Any]] = field(default_factory=list)
    context_trace: Optional[Dict[str, Any]] = None


def append_saved_system_prompt(
    messages: List[Dict[str, str]], prompt: Optional[str]
) -> int:
    """Append the user's saved prompt as the sole system instruction."""
    content = prompt.strip() if prompt else ""
    if not content:
        return 0
    messages.append({"role": "system", "content": content})
    return len(content)


def append_document_access_status(
    messages: List[Dict[str, str]],
    *,
    full_file_names: List[str],
    embedding_file_names: List[str],
    has_grouped_sources: bool,
    has_library_sources: bool,
) -> None:
    """Tell the model what is searchable now versus merely present in history."""
    selected_names = list(dict.fromkeys(full_file_names + embedding_file_names))
    has_current_sources = bool(
        selected_names or has_grouped_sources or has_library_sources
    )

    if has_current_sources:
        lines = ["Current document access for this turn:"]
        if selected_names:
            lines.extend(f"- {name}" for name in selected_names)
        if has_grouped_sources:
            lines.append("- Additional documents selected through tags or folders")
        if has_library_sources:
            lines.append("- Selected shared libraries")
        lines.append(
            "These sources are available for this turn. Retrieved [S#] passages are "
            "only the query-matched subset, so the absence of a passage from a source "
            "does not mean that source is unavailable."
        )
    else:
        lines = [
            "Current document access for this turn: none selected.",
            "Earlier chat messages may still contain information derived from files "
            "that were selected on prior turns. You may use that visible conversation "
            "history, but do not describe it as current access to those files.",
        ]

    messages.append({"role": "user", "content": "\n".join(lines)})


def _retrieval_sources(message_obj: Optional[Any]) -> List[Dict[str, Any]]:
    """The turn's saved RAG trace payloads, normalized to a list.

    ``save_retrieval_trace`` stores one payload as a plain object and wraps
    several (documents AND libraries in one turn) into ``{"traces": [...]}``.
    """
    trace = getattr(message_obj, "retrieval_trace", None) if message_obj else None
    if not trace:
        return []
    if isinstance(trace, dict) and "traces" in trace:
        return trace["traces"]
    return [trace]


@database_sync_to_async
def _persisted_snippets(message_obj: Any) -> List[Dict[str, Any]]:
    """The citation snippets the retrieval stage just persisted for a message.

    Naive mode has no pipeline trace, so this is how its kept snippets reach
    the context trace. Snippet rows from a prior generation can't leak in:
    regeneration deletes them before the turn and fresh messages start empty.
    """
    return [
        {
            "ref": (
                snippet.file.name
                if snippet.file
                else snippet.source_ref or str(snippet.library or "")
            ),
            "score": snippet.similarity_score,
            "preview": " ".join((snippet.text or "").split())[:90],
        }
        for snippet in message_obj.snippets.filter(is_active=True, is_deleted=False)
        .select_related("file", "library")
        .order_by("-similarity_score")
    ]


async def build_standard_messages(
    request: LLMQueryRequest,
    document_processor: DocumentProcessor,
    file_processor: FileProcessor,
) -> MessageBuildResult:
    """
    Build messages for standard (non-Socratic) mode.

    Assembles messages from:
    - System prompt (if provided)
    - Referenced conversation context
    - File contents
    - Semantic document context (via vector search)
    - Memory context (semantic search on user's memory store)
    - Conversation history
    - Current user message

    Every stage that contributes something is recorded on the returned
    ``context_trace`` (with elapsed ms), which the tool loop persists and
    streams to the frontend as the turn's context-assembly trace.

    Args:
        request: LLMQueryRequest containing all query parameters
        document_processor: DocumentProcessor for vector search
        file_processor: FileProcessor for reading file contents

    Returns:
        MessageBuildResult with messages and any memory context items used
    """
    # Extract commonly used values from request
    user_id = request.user.id if request.user else None

    messages = []
    memory_context = []
    trace = ContextTraceRecorder()
    full_file_names: List[str] = []

    # A saved conversation prompt is the user's system prompt. MCP-specific
    # instructions belong to each server's advertised instructions and tool
    # descriptions, not in a DARE-wide prompt injected into every conversation.
    with trace.stage("prompt") as stage:
        prompt = await get_prompt(request.generation.prompt_id)
        prompt_chars = append_saved_system_prompt(messages, prompt)
        if prompt_chars:
            stage["chars"] = prompt_chars

    # Add referenced conversation context
    if request.context.referenced_conversation_ids:
        with trace.stage("referencedConversations") as stage:
            referenced_context = await get_referenced_conversations_context(
                request.context.referenced_conversation_ids,
                user_id,
                request.context.referenced_conversation_history_limit,
            )
            if referenced_context:
                messages.append({"role": "user", "content": referenced_context})
                stage["count"] = len(request.context.referenced_conversation_ids)
                stage["chars"] = len(referenced_context)

    # Add selected conversation summary context
    if request.context.referenced_summary_ids:
        with trace.stage("summaries") as stage:
            summary_context = await get_referenced_summaries_context(
                request.context.referenced_summary_ids,
            )
            if summary_context:
                messages.append({"role": "user", "content": summary_context})
                stage["count"] = len(request.context.referenced_summary_ids)

    # Add full file contents
    if request.context.file_ids:
        with trace.stage("files") as stage:
            file_contents = await get_full_file_contents(
                request.context.file_ids, file_processor
            )
            for file_content in file_contents:
                messages.append({"role": "user", "content": file_content["content"]})
            if file_contents:
                full_file_names = [item["name"] for item in file_contents]
                stage["files"] = [
                    {"name": item["name"], "chars": len(item["content"])}
                    for item in file_contents
                ]

    # Add semantic context from vector search
    has_retrieval_sources = bool(
        request.context.embedding_ids
        or request.context.tag_ids
        or request.context.folder_ids
        or request.context.library_ids
    )
    with trace.stage("retrieval") as stage:
        before_retrieval = len(messages)
        retrieval_failures = await add_semantic_context_to_messages(
            document_processor=document_processor,
            messages=messages,
            query=request.message,
            embedding_ids=request.context.embedding_ids,
            tag_ids=request.context.tag_ids,
            folder_ids=request.context.folder_ids,
            library_ids=request.context.library_ids,
            user_id=user_id,
            file_owner_id=request.context.file_owner_id,
            is_socratic_mode=request.is_socratic_mode(),
            similarity_threshold=request.context.document_similarity_threshold,
            max_context_snippets=request.context.max_context_snippets,
            rag_mode=request.context.rag_mode,
            message_obj=request.message_obj,
            workflow_run_step_obj=request.workflow_run_step_obj,
        )
        if has_retrieval_sources:
            stage["mode"] = request.context.rag_mode
            stage["threshold"] = request.context.document_similarity_threshold
            stage["topK"] = request.context.max_context_snippets
            stage["injectedBlocks"] = len(messages) - before_retrieval
            if retrieval_failures:
                # Without this the UI cannot tell "nothing matched" from "the
                # vector store never answered" — both render as zero blocks.
                stage["failed"] = True
                stage["errors"] = retrieval_failures
            sources = _retrieval_sources(request.message_obj)
            if sources:
                stage["sources"] = sources
            elif (
                request.message_obj is not None
                and request.context.rag_mode == RagMode.NAIVE
            ):
                # Naive mode runs no traced pipeline; surface the kept
                # snippets it persisted so the trace still shows what the
                # threshold and top-k actually did.
                snippets = await _persisted_snippets(request.message_obj)
                if snippets:
                    stage["snippets"] = snippets

    # Add memory context (semantic search against user's memory store)
    if request.context.use_memory and user_id:
        with trace.stage("memory") as stage:
            memory_context = await add_memory_context_to_messages(
                messages=messages,
                query=request.message,
                user_id=user_id,
            )
            if memory_context:
                stage["count"] = len(memory_context)
    elif user_id:
        # Memory OFF is not just "inject nothing" — left unsaid, the model
        # improvises. Red-teamed: asked to remember a code with the toggle
        # off, it answered "I've noted that" and later "I've updated my
        # notes", while nothing was written anywhere. The model cannot know
        # the toggle exists unless it is told, so the one honest line rides
        # every memory-off turn for a signed-in user.
        messages.append(
            {
                "role": "user",
                "content": (
                    "<memory_status>\nMemory is OFF for this conversation: "
                    "nothing said here is remembered across chats, and "
                    "nothing can be recalled from other chats. If the person "
                    "asks you to remember something, say plainly that memory "
                    "is off for this conversation and that they can turn it "
                    "on from the conversation context menu — never claim to "
                    "have noted or saved anything.\n</memory_status>"
                ),
            }
        )

    # Add conversation history
    with trace.stage("history") as stage:
        conversation_history = (
            await get_conversation_history(
                request.conversation, limit=request.context.history_limit
            )
            if request.conversation
            else []
        )
        # Keep turns that carry tool_calls even when their text content is empty —
        # dropping them would orphan the role:"tool" results that follow. The same
        # goes for role:"tool" turns themselves: every tool_call id MUST keep its
        # result turn or providers reject the request, so they bypass the
        # empty-content filter entirely.
        history_messages = [
            msg
            for msg in conversation_history
            if msg.get("role") == "tool"
            or msg.get("tool_calls")
            or (isinstance(msg.get("content"), str) and msg["content"].strip())
        ]
        messages.extend(history_messages)
        if history_messages:
            stage["turns"] = len(history_messages)
            stage["limit"] = request.context.history_limit

    # Keep current source availability adjacent to the current question. This
    # prevents the model from mistaking "one matched snippet" for "only one
    # selected file", and makes deselection semantics explicit even while old
    # file-derived answers remain in the conversation-history window.
    embedding_file_names = await get_selected_file_names(
        request.context.embedding_ids,
        user_id,
        request.context.file_owner_id,
    )
    append_document_access_status(
        messages,
        full_file_names=full_file_names,
        embedding_file_names=embedding_file_names,
        has_grouped_sources=bool(request.context.tag_ids or request.context.folder_ids),
        has_library_sources=bool(request.context.library_ids),
    )

    # Add current user message (verbatim — labels like "User's message:" add
    # nothing and pollute few-shot structure)
    messages.append({"role": "user", "content": request.message})

    return MessageBuildResult(
        messages=messages,
        memory_context=memory_context,
        context_trace=trace.to_payload(),
    )
