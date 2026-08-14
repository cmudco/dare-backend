"""Post-reply ingestion on the ordered ``memory`` queue.
One worker preserves turn order; failures remain in RQ's failed registry."""

import logging

from django.db import close_old_connections
from django_rq import job

from config.env import USE_POSTGRES
from conversations.constants import SenderType
from conversations.models import Message
from memory.models import MemoryLedgerEntry
from memory.services.ingest import ingest_turn
from memory.services.notify import announce_write, summarize_report

logger = logging.getLogger(__name__)

MEMORY_QUEUE = "memory"


@job(MEMORY_QUEUE)
def run_memory_writer(ai_message_id: int) -> None:
    # Refresh stale database connections in the long-lived worker.
    close_old_connections()

    if not USE_POSTGRES:
        logger.debug("[memory] writer skipped: USE_POSTGRES is False")
        return

    ai_message = (
        Message.active_objects.select_related("conversation", "conversation__user")
        .filter(pk=ai_message_id)
        .first()
    )
    if ai_message is None:
        logger.warning("[memory] writer: message %s vanished", ai_message_id)
        return

    conversation = ai_message.conversation
    user = conversation.user
    if user is None:
        # Public conversations have no user-owned memory store.
        return

    user_message = (
        Message.active_objects.filter(
            conversation=conversation,
            sender_type=SenderType.PLAYER,
            id__lt=ai_message.id,
            is_deleted=False,
        )
        .order_by("-id")
        .first()
    )
    if user_message is None:
        return

    # A ledger row proves this user turn was already committed.
    if MemoryLedgerEntry.objects.filter(source_message=user_message).exists():
        logger.info(
            "[memory] writer: message %s already ingested, skipping", user_message.id
        )
        return

    report = ingest_turn(user, conversation, user_message, ai_message)

    if report.skipped:
        logger.info("[memory] turn %s skipped: %s", user_message.id, report.skipped)
        return

    # Persist the memory chip before broadcasting it to the conversation.
    summary = summarize_report(report, report.entries)
    ai_message.memory_write_data = summary
    ai_message.save(update_fields=["memory_write_data", "updated_at"])
    # Clients subscribe with the public conversation ID.
    announce_write(conversation.conversation_id, ai_message.id, summary)

    logger.info(
        "[memory] turn %s: %d decisions → %d created, %d retired, %d reinforced, "
        "doc_changed=%s",
        user_message.id,
        report.decisions,
        len(report.created),
        report.retired,
        report.reinforced,
        report.user_doc_changed,
    )
