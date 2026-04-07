import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django_rq import job

from conversations.constants import SenderType
from conversations.models import Conversation, ConversationSummary, Message
from conversations.services.summary_service import generate_conversation_summary

logger = logging.getLogger(__name__)

MESSAGES_PER_SUMMARY = getattr(
    settings,
    "SUMMARY_MESSAGES_PER_GROUP",
    5,
)
SUMMARY_QUEUE = "default"


@job(SUMMARY_QUEUE)
def refresh_conversation_summary_for_conversation(
    conversation_pk: int,
) -> dict[str, Any]:
    """Create or update the rolling summary for a single conversation."""
    try:
        conversation = Conversation.active_objects.get(pk=conversation_pk)
    except Conversation.DoesNotExist:
        logger.warning(
            "Conversation summary skipped: conversation %s not found",
            conversation_pk,
        )
        return {"status": "skipped", "reason": "conversation_not_found"}

    if conversation.user_id is None:
        return {"status": "skipped", "reason": "anonymous_conversation"}

    completed_message_count = _get_completed_message_count(conversation_pk)
    if completed_message_count < MESSAGES_PER_SUMMARY:
        return {
            "status": "skipped",
            "reason": "not_enough_completed_messages",
            "completed_count": completed_message_count,
        }

    summary_message_count = completed_message_count - (
        completed_message_count % MESSAGES_PER_SUMMARY
    )
    if summary_message_count == 0:
        return {
            "status": "skipped",
            "reason": "threshold_not_reached",
            "completed_count": completed_message_count,
        }

    existing_summary = ConversationSummary.active_objects.filter(
        conversation=conversation
    ).first()
    if (
        existing_summary is not None
        and existing_summary.summarized_message_count >= summary_message_count
    ):
        return {
            "status": "skipped",
            "reason": "summary_up_to_date",
            "completed_count": completed_message_count,
            "summarized_message_count": existing_summary.summarized_message_count,
        }

    summary_result = generate_conversation_summary(
        conversation,
        summary_message_count,
    )
    if not summary_result.summary:
        logger.warning(
            "Conversation summary generation returned empty output for conversation %s",
            conversation.conversation_id,
        )
        return {"status": "skipped", "reason": "empty_summary"}

    with transaction.atomic():
        if existing_summary is None:
            summary = ConversationSummary.active_objects.create(
                conversation=conversation,
                summary=summary_result.summary,
                llm=summary_result.llm,
                input_tokens=summary_result.input_tokens,
                output_tokens=summary_result.output_tokens,
                summarized_message_count=summary_message_count,
            )
            action = "created"
        else:
            existing_summary.summary = summary_result.summary
            existing_summary.llm = summary_result.llm
            existing_summary.input_tokens = summary_result.input_tokens
            existing_summary.output_tokens = summary_result.output_tokens
            existing_summary.summarized_message_count = summary_message_count
            existing_summary.save(
                update_fields=[
                    "summary",
                    "llm",
                    "input_tokens",
                    "output_tokens",
                    "summarized_message_count",
                    "updated_at",
                ]
            )
            summary = existing_summary
            action = "updated"

    return {
        "status": "ok",
        "action": action,
        "conversation_id": conversation.conversation_id,
        "completed_count": completed_message_count,
        "summarized_message_count": summary.summarized_message_count,
        "summary_id": summary.id,
    }


def _get_completed_message_count(conversation_pk: int) -> int:
    """Return the number of completed AI assistant messages in the conversation."""
    return Message.active_objects.filter(
        conversation_id=conversation_pk,
        sender_type=SenderType.AI_ASSISTANT,
    ).count()
