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

from conversations.constants import SenderType
from conversations.models import (
    Artifact,
    Conversation,
    ConversationSummary,
    Message,
    Snippet,
)
from files.models import File
from prompts.models import Prompt

logger = logging.getLogger(__name__)

# Maximum artifact content to include in LLM context (chars, ~2k tokens)
MAX_ARTIFACT_CONTENT_IN_CONTEXT = 8000


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


@database_sync_to_async
def get_conversation_history(conversation: Conversation, limit: int = 10) -> list:
    """Retrieves recent chat history for AI context, including tool results and artifacts.

    For assistant messages with tool calls, appends the tool results to give
    the LLM context for follow-up messages (e.g., modifying a chart).

    For assistant messages with artifacts, includes the LATEST VERSION of each
    artifact group to enable intelligent inline edits using string replacement.

    Args:
        conversation: Conversation instance
        limit: Maximum number of messages to retrieve

    Returns:
        List of message dictionaries with role and content
    """
    messages = (
        Message.active_objects.filter(conversation=conversation)
        .prefetch_related("mcp_tool_calls", "artifacts__artifact_group__latest_version")
        .order_by("-created_at")
    )

    if limit >= 50:
        messages = messages[2:]
    else:
        messages = messages[2 : limit + 2] if limit > 0 else messages[2:]

    # Track artifact groups to only include latest version once
    included_artifact_groups = set()

    result = []
    for msg in reversed(messages):
        role = "user" if msg.sender_type == SenderType.PLAYER else "assistant"
        content = msg.message

        # For assistant messages, include tool call results and artifacts
        if msg.sender_type == SenderType.AI_ASSISTANT:
            # Include tool call results from MessageToolCall
            tool_calls = msg.mcp_tool_calls.all()
            if tool_calls.exists():
                tool_results_text = []
                for tc in tool_calls:
                    if tc.result:
                        tool_results_text.append(f"[Tool: {tc.tool_name}]\n{tc.result}")
                if tool_results_text:
                    content = f"{content}\n\n--- Tool Results ---\n" + "\n\n".join(
                        tool_results_text
                    )

            # Include LATEST artifact content for LLM context
            # This ensures the LLM sees current state for accurate inline edits
            artifacts = msg.artifacts.filter(is_deleted=False)
            for artifact in artifacts:
                group = artifact.artifact_group
                if not group:
                    continue
                group_id = group.id

                # Skip if we've already included the latest version from this group
                if group_id in included_artifact_groups:
                    logger.debug(
                        f"[get_conversation_history] Skipping artifact #{artifact.id} "
                        f"(group {group_id} already included)"
                    )
                    continue
                included_artifact_groups.add(group_id)

                # Use the LATEST version from the group, not the version attached to this message
                latest_artifact = group.latest_version
                if not latest_artifact:
                    latest_artifact = artifact  # Fallback to current if no latest set

                # Build artifact context block with latest content
                artifact_context = _format_artifact_for_history(latest_artifact)
                content = f"{content}\n\n{artifact_context}"
                logger.info(
                    f"[get_conversation_history] Including artifact #{latest_artifact.id} "
                    f"(type={latest_artifact.artifact_type}, v{latest_artifact.version}, group={group_id}) "
                    f"as LATEST version in message {msg.id}"
                )

        result.append({"role": role, "content": content})

    logger.info(
        f"[get_conversation_history] Returning {len(result)} messages, "
        f"{len(included_artifact_groups)} artifact groups included"
    )
    return result


def _format_artifact_for_history(artifact: "Artifact") -> str:
    """Format a single artifact for inclusion in conversation history.

    Provides structured context that helps the LLM understand:
    - Artifact identity (ID, title, group)
    - Type and version for context
    - Complete content for string-based modifications

    Args:
        artifact: Artifact model instance

    Returns:
        Formatted string with artifact metadata and content
    """
    # Truncate very large artifacts to prevent context explosion
    content_to_include = artifact.content
    if len(content_to_include) > MAX_ARTIFACT_CONTENT_IN_CONTEXT:
        content_to_include = (
            content_to_include[:MAX_ARTIFACT_CONTENT_IN_CONTEXT]
            + f"\n... [truncated, {len(artifact.content)} total chars]"
        )

    return f"""[Artifact #{artifact.id}] {artifact.title}
Type: {artifact.artifact_type} | Version: v{artifact.version} | Group: {artifact.artifact_group_id}
--- Content ---
{content_to_include}
--- End Content ---"""


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
            similarity_score=chunk.score,
            chunk_index=chunk.chunk_index,
        )
    except Exception as exc:
        logger.warning("Failed to save library snippet: %s", exc)


def save_retrieval_trace(message_obj, payload) -> None:
    """Persist the RAG pipeline trace onto a message (best-effort, never raises).

    Sync (called from an already-sync context). ``payload`` is the camelized dict
    from ``RetrievalTrace.to_payload()``.
    """
    try:
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
        List of formatted file content strings
    """
    if not file_ids:
        return []

    file_contents = []
    files = File.active_objects.filter(id__in=file_ids)
    for file in files:
        try:
            content = file_processor.read_file_content(file)
            file_name = file.name or file.file.name
            formatted_content = f"File: {file_name}\n\n{content}"
            file_contents.append(formatted_content)
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
