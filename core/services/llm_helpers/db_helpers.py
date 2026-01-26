"""
Database Helper Functions for LLM Service

Standalone `@database_sync_to_async` functions for database operations.
These functions are extracted from LLMService to improve modularity.

All functions are stateless - they receive the required models and dependencies
as parameters instead of accessing class instance state.
"""

from typing import Optional, List, Callable
import base64
import logging
from channels.db import database_sync_to_async

from conversations.models import Conversation, Message
from conversations.constants import SenderType
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


@database_sync_to_async
def get_conversation_history(conversation: Conversation, limit: int = 10) -> list:
    """Retrieves recent chat history for AI context, ignoring placeholders.
    
    Args:
        conversation: Conversation instance
        limit: Maximum number of messages to retrieve
        
    Returns:
        List of message dictionaries with role and content
    """
    messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')
    if limit >= 50:
        messages = messages[2:]
    else:
        messages = messages[2:limit+2] if limit > 0 else messages[2:]
    return [
        {"role": "user" if msg.sender_type == SenderType.PLAYER else "assistant", "content": msg.message}
        for msg in reversed(messages)
    ]


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
    return list(File.active_objects.filter(
        tags__id__in=tag_ids, user_id=user_id
    ).distinct().values_list('id', flat=True))


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
    return list(File.active_objects.filter(
        folders__id__in=folder_ids, user_id=user_id
    ).distinct().values_list('id', flat=True))


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
    return list(File.active_objects.filter(
        id__in=media_ids,
        media_type__in=['audio', 'video']
    ))


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


def convert_file_to_base64_dict(media_file: 'File') -> Optional[dict]:
    """Convert a single media file to base64 data URL dict for vision API.
    
    This is a synchronous helper used by get_media_files_as_images.
    
    Args:
        media_file: File object to convert
        
    Returns:
        Dict with 'preview', 'name', 'type' or None if conversion fails
    """
    try:
        with media_file.file.open('rb') as f:
            file_data = f.read()
        
        base64_data = base64.b64encode(file_data).decode('utf-8')
        data_url = f"data:{media_file.file_type};base64,{base64_data}"
        
        return {
            'preview': data_url,
            'name': media_file.name or media_file.file.name,
            'type': media_file.file_type
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
        id__in=media_ids,
        user_id=user_id,
        is_media=True
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
    history_limit: int = None
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
        conversation_id__in=conversation_ids,
        user_id=user_id
    )

    for conversation in conversations:
        messages_query = Message.active_objects.filter(
            conversation=conversation
        ).order_by('-created_at')

        if history_limit is not None:
            messages_query = messages_query[:history_limit]

        messages = list(messages_query)

        if messages:
            conversation_title = conversation.title or "Untitled Conversation"
            context_parts.append(f"=== Referenced Conversation: {conversation_title} ===")

            for msg in reversed(messages):
                role = "User" if msg.sender_type == SenderType.PLAYER else "Assistant"
                context_parts.append(f"{role}: {msg.message}")

            context_parts.append("=== End of Referenced Conversation ===\n")

    if context_parts:
        full_context = "\n".join(context_parts)
        return f"Referenced conversation context for additional background:\n\n{full_context}"

    return ""
