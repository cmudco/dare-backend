import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from conversations.constants import SenderType
from conversations.models import Message
from conversations.tasks import refresh_conversation_summary_for_conversation

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Message)
def enqueue_conversation_summary_refresh(
    sender,
    instance: Message,
    created: bool,
    **kwargs,
) -> None:
    """Enqueue rolling summary generation after a new AI message is saved."""
    if not created or instance.sender_type != SenderType.AI_ASSISTANT:
        return

    if instance.conversation.user_id is None:
        return

    try:
        refresh_conversation_summary_for_conversation.delay(instance.conversation_id)
    except Exception:
        logger.exception(
            "Failed to enqueue conversation summary for conversation %s",
            instance.conversation.conversation_id,
        )
