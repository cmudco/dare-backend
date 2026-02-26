"""
Memory Extraction Background Tasks

Handles automatic extraction of user memories from conversations.
Uses django-rq for background job processing.
"""

import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from django_rq import job
from asgiref.sync import async_to_sync

from conversations.models import Conversation

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURABLE CONSTANTS
# =============================================================================

# Minimum number of messages required before extracting memories
MIN_MESSAGE_COUNT = 5

# Minimum time (in hours) since last extraction before re-extracting
EXTRACTION_COOLDOWN_HOURS = 1

# Batch size for processing conversations
BATCH_SIZE = 50


@job
def process_memory_extraction():
    """
    Extract memories from eligible conversations.
    
    A conversation is eligible if:
    1. It has >= MIN_MESSAGE_COUNT messages
    2. Either:
       a. Never been extracted (last_memory_extracted_at is NULL), OR
       b. Has new messages since last extraction AND cooldown period has passed
    
    Returns:
        dict: Stats about the extraction run
    """
    from memory.services import get_memu_service
    
    stats = {
        "total_checked": 0,
        "eligible": 0,
        "processed": 0,
        "failed": 0,
        "skipped_low_message_count": 0,
        "skipped_recently_extracted": 0,
        "skipped_no_new_messages": 0,
    }
    
    try:
        memu_service = get_memu_service()
        # Initialize once before batch; fail fast if memu-py/config is broken
        async_to_sync(memu_service._ensure_initialized)()
    except Exception as e:
        logger.error(f"Failed to initialize MemU service: {e}")
        return {"status": "error", "message": str(e)}
    
    cooldown_cutoff = timezone.now() - timedelta(hours=EXTRACTION_COOLDOWN_HOURS)
    
    # Get conversations with message counts
    conversations = (
        Conversation.active_objects
        .filter(user__isnull=False)  # Only user conversations, not anonymous
        .annotate(message_count=Count('messages'))
        .order_by('last_memory_extracted_at')  # Process oldest first
        [:BATCH_SIZE]
    )
    
    for conv in conversations:
        stats["total_checked"] += 1
        
        # Check minimum message count
        if conv.message_count < MIN_MESSAGE_COUNT:
            stats["skipped_low_message_count"] += 1
            continue
        
        # Check if never extracted
        if conv.last_memory_extracted_at is None:
            is_eligible = True
        else:
            # Check cooldown
            if conv.last_memory_extracted_at > cooldown_cutoff:
                stats["skipped_recently_extracted"] += 1
                continue
            
            # Check for new messages since last extraction
            has_new_messages = conv.messages.filter(
                created_at__gt=conv.last_memory_extracted_at
            ).exists()
            
            if not has_new_messages:
                stats["skipped_no_new_messages"] += 1
                continue
            
            is_eligible = True
        
        if is_eligible:
            stats["eligible"] += 1
            success = _extract_conversation_memories(conv, memu_service)
            
            if success:
                stats["processed"] += 1
            else:
                stats["failed"] += 1
    
    logger.info(f"Memory extraction completed: {stats}")
    return stats


def _extract_conversation_memories(conversation: Conversation, memu_service) -> bool:
    """
    Extract memories from a single conversation.
    
    Args:
        conversation: The conversation to process
        memu_service: Initialized MemU service instance
        
    Returns:
        bool: True if extraction succeeded, False otherwise
    """
    try:
        user_id = str(conversation.user.id)
        
        # Get messages (only since last extraction if applicable)
        messages_qs = conversation.messages.all().order_by('created_at')
        
        if conversation.last_memory_extracted_at:
            messages_qs = messages_qs.filter(
                created_at__gt=conversation.last_memory_extracted_at
            )
        
        # Format messages for MemU
        messages = [
            {
                "role": "user" if msg.sender_type == 1 else "assistant",
                "content": msg.message
            }
            for msg in messages_qs
        ]
        
        if not messages:
            logger.debug(f"No messages to extract for conversation {conversation.id}")
            return True
        
        # Call MemU synchronously (wrap async)
        async_to_sync(memu_service.memorize_conversation)(user_id, messages)
        
        # Update tracking timestamp
        conversation.last_memory_extracted_at = timezone.now()
        conversation.save(update_fields=['last_memory_extracted_at'])
        
        logger.info(
            f"Extracted memories from conversation {conversation.id} "
            f"({len(messages)} messages) for user {user_id}"
        )
        return True
        
    except Exception as e:
        logger.error(
            f"Failed to extract memories from conversation {conversation.id}: {e}"
        )
        return False


@job
def extract_single_conversation(conversation_id: int):
    """
    Extract memories from a specific conversation.
    Can be triggered manually or as a one-off job.
    
    Args:
        conversation_id: ID of the conversation to process
        
    Returns:
        dict: Result of the extraction
    """
    from memory.services import get_memu_service
    
    try:
        conversation = Conversation.active_objects.get(id=conversation_id)
    except Conversation.DoesNotExist:
        return {"status": "error", "message": "Conversation not found"}
    
    if not conversation.user:
        return {"status": "error", "message": "Anonymous conversation, skipping"}
    
    try:
        memu_service = get_memu_service()
        success = _extract_conversation_memories(conversation, memu_service)
        
        if success:
            return {
                "status": "success",
                "conversation_id": conversation_id,
                "message": "Memories extracted successfully"
            }
        else:
            return {
                "status": "failed",
                "conversation_id": conversation_id,
                "message": "Extraction failed, check logs"
            }
            
    except Exception as e:
        return {
            "status": "error",
            "conversation_id": conversation_id,
            "message": str(e)
        }
