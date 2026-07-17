"""
Database Helper Functions for LLM Service

Standalone `@database_sync_to_async` functions for database operations.
These functions are extracted from LLMService to improve modularity.

All functions are stateless - they receive the required models and dependencies
as parameters instead of accessing class instance state.
"""

import base64
import logging
from typing import Callable, List, Optional

from channels.db import database_sync_to_async

from conversations.models import ConversationSummary, Snippet
from files.models import File
from prompts.models import Prompt

logger = logging.getLogger(__name__)


@database_sync_to_async
def get_prompt(prompt_id: str = None) -> str:
    """Fetches the prompt if the prompt_id is provided.

    Args:
        prompt_id: UUID of the prompt to fetch

    Returns:
        Prompt content string or empty string
    """
    if prompt_id:
        prompt = Prompt.active_objects.filter(id=prompt_id).first()
        return prompt.content if prompt else ""
    return ""


def save_library_snippet(message_obj, chunk) -> None:
    """Persist one library citation snippet for a message (best-effort, never raises).

    Called from an already-sync context, so it is a plain function (not
    ``@database_sync_to_async``). ``chunk`` is a ``core.services.rag`` RetrievedChunk,
    duck-typed here to avoid a cross-package import.
    """
    try:
        Snippet.active_objects.create(
            message=message_obj,
            file=None,
            library=chunk.library,
            source_ref=chunk.source_ref,
            text=chunk.text,
            similarity_score=(
                chunk.rerank_score if chunk.rerank_score is not None else chunk.score
            ),
            chunk_index=chunk.chunk_index,
        )
    except Exception as exc:
        logger.warning("Failed to save library snippet: %s", exc)


def save_document_snippet(message_obj, chunk) -> None:
    """Persist one document citation snippet for a message (best-effort, never raises).

    Document twin of ``save_library_snippet``: same duck-typed
    ``core.services.rag`` RetrievedChunk, but resolved to a ``File`` row. Prefers
    the calibrated rerank score so the UI shows the score that ranked the chunk.
    """
    try:
        file = File.active_objects.get(id=int(chunk.file_id))
        Snippet.active_objects.create(
            message=message_obj,
            file=file,
            library=None,
            text=chunk.text,
            similarity_score=(
                chunk.rerank_score if chunk.rerank_score is not None else chunk.score
            ),
            chunk_index=chunk.chunk_index,
        )
    except Exception as exc:
        logger.warning("Failed to save document snippet: %s", exc)


def save_retrieval_trace(message_obj, payload) -> None:
    """Persist the RAG pipeline trace onto a message (best-effort, never raises).

    Sync (called from an already-sync context). ``payload`` is the camelized dict
    from ``RetrievalTrace.to_payload()``.

    A message can retrieve from more than one source in a single turn (uploaded
    documents AND shared libraries), each producing its own trace. The first
    trace is stored as a plain object (the shape the frontend has always read);
    a second one in the same turn wraps both into ``{"traces": [...]}``. Callers
    must reset ``message_obj.retrieval_trace`` at the start of a turn so traces
    from a previous generation never accumulate.
    """
    try:
        existing = message_obj.retrieval_trace
        if existing:
            traces = existing.get("traces") if isinstance(existing, dict) else None
            if traces is None:
                traces = [existing]
            message_obj.retrieval_trace = {"traces": traces + [payload]}
        else:
            message_obj.retrieval_trace = payload
        message_obj.save(update_fields=["retrieval_trace"])
    except Exception as exc:
        logger.warning("Failed to save retrieval trace: %s", exc)


@database_sync_to_async
def get_files_from_tags(tag_ids: list, user_id: int) -> list:
    """Fetch file IDs from tags.

    Args:
        tag_ids: List of tag IDs
        user_id: User ID for filtering

    Returns:
        List of file IDs
    """
    if not tag_ids:
        return []
    return list(
        File.active_objects.filter(tags__id__in=tag_ids, user_id=user_id)
        .distinct()
        .values_list("id", flat=True)
    )


@database_sync_to_async
def get_files_from_folders(folder_ids: list, user_id: int) -> list:
    """Fetch file IDs from folders.

    Args:
        folder_ids: List of folder IDs
        user_id: User ID for filtering

    Returns:
        List of file IDs
    """
    if not folder_ids:
        return []
    return list(
        File.active_objects.filter(folders__id__in=folder_ids, user_id=user_id)
        .distinct()
        .values_list("id", flat=True)
    )


@database_sync_to_async
def get_audio_or_video_files(media_ids: list) -> list:
    """Fetch audio/video File objects by IDs for transcription.

    Args:
        media_ids: List of media file IDs

    Returns:
        List of File objects
    """
    if not media_ids:
        return []
    return list(
        File.active_objects.filter(id__in=media_ids, media_type__in=["audio", "video"])
    )


@database_sync_to_async
def get_full_file_contents(file_ids: list, file_processor) -> list:
    """Read full content from files for the given file IDs.

    Args:
        file_ids: List of file IDs
        file_processor: FileProcessor instance for reading file content

    Returns:
        List of ``{"name", "content"}`` dicts, where ``content`` is the
        formatted block to inject into the prompt
    """
    if not file_ids:
        return []

    file_contents = []
    files = File.active_objects.filter(id__in=file_ids)
    for file in files:
        try:
            content = file_processor.read_file_content(file)
            file_name = file.name or file.file.name
            file_contents.append(
                {"name": file_name, "content": f"File: {file_name}\n\n{content}"}
            )
        except Exception:
            continue

    return file_contents


def convert_file_to_base64_dict(media_file: "File") -> Optional[dict]:
    """Convert a single media file to base64 data URL dict for vision API.

    This is a synchronous helper used by get_media_files_as_images.

    Args:
        media_file: File object to convert

    Returns:
        Dict with 'preview', 'name', 'type' or None if conversion fails
    """
    try:
        with media_file.file.open("rb") as f:
            file_data = f.read()

        base64_data = base64.b64encode(file_data).decode("utf-8")
        data_url = f"data:{media_file.file_type};base64,{base64_data}"

        return {
            "preview": data_url,
            "name": media_file.name or media_file.file.name,
            "type": media_file.file_type,
        }
    except Exception as e:
        logger.error(f"Error reading media file {media_file.id}: {str(e)}")
        return None


@database_sync_to_async
def get_media_files_as_images(media_ids: list, user_id: int) -> list:
    """Convert media file IDs to image format for LLM vision API.

    Reads media files from disk and converts to base64 data URLs.

    Args:
        media_ids: List of media file IDs
        user_id: User ID for filtering

    Returns:
        List of dicts with 'preview' (base64 data URL), 'name', 'type'
    """
    if not media_ids:
        return []

    media_images = []
    media_files = File.active_objects.filter(
        id__in=media_ids, user_id=user_id, is_media=True
    )

    for media_file in media_files:
        result = convert_file_to_base64_dict(media_file)
        if result:
            media_images.append(result)

    return media_images


@database_sync_to_async
def get_referenced_conversations_context(
    conversation_ids: list,
    user_id: int,
    history_limit: Optional[int] = None,
) -> str:
    """Fetch context from referenced conversations.

    Args:
        conversation_ids: List of conversation IDs to fetch
        user_id: User ID for filtering
        history_limit: Optional limit for messages (None = all messages)

    Returns:
        Formatted context string from referenced conversations
    """
    if not conversation_ids:
        return ""

    context_parts = []
    conversations = Conversation.active_objects.filter(
        conversation_id__in=conversation_ids, user_id=user_id
    )

    for conversation in conversations:
        messages_query = Message.active_objects.filter(
            conversation=conversation
        ).order_by("-created_at")

        if history_limit is not None:
            messages_query = messages_query[:history_limit]

        messages = list(messages_query)

        if messages:
            conversation_title = conversation.title or "Untitled Conversation"
            context_parts.append(
                f"=== Referenced Conversation: {conversation_title} ==="
            )

            for msg in reversed(messages):
                role = "User" if msg.sender_type == SenderType.PLAYER else "Assistant"
                context_parts.append(f"{role}: {msg.message}")

            context_parts.append("=== End of Referenced Conversation ===\n")

    if context_parts:
        full_context = "\n".join(context_parts)
        return f"Referenced conversation context for additional background:\n\n{full_context}"

    return ""


@database_sync_to_async
def get_referenced_summaries_context(summary_ids: List[int]) -> str:
    """Fetch context from selected conversation summaries.

    Args:
        summary_ids: List of ConversationSummary primary keys

    Returns:
        Formatted context string with the selected summaries
    """
    if not summary_ids:
        return ""

    summaries = ConversationSummary.active_objects.filter(
        id__in=summary_ids
    ).select_related("conversation")

    context_parts = []
    for summary in summaries:
        title = summary.conversation.title or "Untitled Conversation"
        context_parts.append(f"=== Conversation Summary: {title} ===")
        context_parts.append(summary.summary)
        context_parts.append("=== End of Summary ===\n")

    if not context_parts:
        return ""

    full_context = "\n".join(context_parts)
    return f"Conversation summary context for additional background:\n\n{full_context}"
