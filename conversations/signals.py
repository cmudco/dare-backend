import logging

from django.conf import settings
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from billing.models import Transaction
from conversations.constants import SenderType
from conversations.models import LLM, Message
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
    if not settings.CONVERSATION_SUMMARY_JOBS_ENABLED:
        return
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


@receiver(pre_delete, sender=LLM)
def keep_model_name_on_history(sender, instance: LLM, **kwargs) -> None:
    """Deleting a model nulls ``llm`` on its history; record the name first."""
    Message._base_manager.filter(llm=instance, llm_name__isnull=True).update(
        llm_name=instance.name
    )
    Transaction._base_manager.filter(llm=instance, llm_name__isnull=True).update(
        llm_name=instance.name
    )
