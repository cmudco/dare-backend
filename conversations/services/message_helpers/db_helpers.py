"""
Database Helper Functions for Message Coordinator

Standalone `@database_sync_to_async` functions for database operations.
These functions are extracted from MessageCoordinator to improve modularity.

All functions are stateless - they receive the required models (conversation,
message, etc.) as parameters instead of accessing class instance state.
"""

from typing import Optional, List, Dict
from channels.db import database_sync_to_async

from conversations.models import Conversation, Message, LLM
from conversations.constants import SenderType


@database_sync_to_async
def get_ai_message_by_id(message_id: int) -> Optional[Message]:
    """Fetch an AI message by ID with LLM relation loaded.
    
    Args:
        message_id: ID of the AI message to fetch
        
    Returns:
        Message instance or None if not found
    """
    return Message.active_objects.select_related('llm').filter(
        id=message_id, sender_type=SenderType.AI_ASSISTANT
    ).first()


@database_sync_to_async
def get_message_media_file_ids(message: Message) -> List[int]:
    """Get audio/video file IDs attached to a message.
    
    Args:
        message: Message instance to get media files from
        
    Returns:
        List of file IDs for audio/video files
    """
    return list(message.files.filter(
        media_type__in=['audio', 'video']
    ).values_list('id', flat=True))


@database_sync_to_async
def fetch_llm_by_id(llm_id: str) -> Optional[LLM]:
    """Fetch LLM by ID from database.
    
    Args:
        llm_id: UUID of the LLM to fetch
        
    Returns:
        LLM instance or None if not found
    """
    return LLM.objects.filter(id=llm_id).first()


@database_sync_to_async
def get_conversation_default_llm(conversation: Conversation) -> Optional[LLM]:
    """Get conversation's selected model or first available LLM.
    
    Args:
        conversation: Conversation instance
        
    Returns:
        LLM instance (selected or first available)
    """
    return conversation.selected_model or LLM.objects.first()


@database_sync_to_async
def fetch_preceding_user_message(conversation: Conversation) -> Optional[Message]:
    """Get the most recent user message in the conversation.
    
    Args:
        conversation: Conversation instance
        
    Returns:
        Most recent user message or None
    """
    return conversation.messages.filter(
        sender_type=SenderType.PLAYER
    ).order_by('-created_at').first()


@database_sync_to_async
def should_generate_title(conversation: Conversation) -> bool:
    """Check if we should generate a conversation title (first message pair).
    
    Args:
        conversation: Conversation instance
        
    Returns:
        True if this is the first user+AI message pair
    """
    return conversation.messages.count() == 2  # User + AI = 2 messages


@database_sync_to_async
def update_message_learning_progress(
    message_obj: 'Message',
    assessment,
    learning_goals: str,
    tracking_prompt: str,
    progress_llm,
    last_usage: Optional[Dict]
) -> 'Message':
    """Update message with learning progress data.
    
    Args:
        message_obj: Message to update
        assessment: Saved progress assessment
        learning_goals: Learning goals text
        tracking_prompt: Tracking prompt text
        progress_llm: LLM used for assessment
        last_usage: Final usage data
        
    Returns:
        Updated message instance
    """
    message_obj.learning_progress_data = {
        "progress_assessment_id": str(getattr(assessment, "id", "")),
        "learning_goals": learning_goals,
        "tracking_prompt": tracking_prompt,
        "llm_id": getattr(progress_llm, "id", None),
        "input_tokens": (last_usage or {}).get("input_tokens"),
        "output_tokens": (last_usage or {}).get("output_tokens"),
        "status": "completed",
    }
    message_obj.save(update_fields=["learning_progress_data"])
    return message_obj
