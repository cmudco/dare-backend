"""The post-reply memory writer job.

Enqueued by MessageCoordinator after a reply is finalized; runs on the
dedicated ``memory`` queue.

DEPLOYMENT INVARIANT: exactly ONE worker drains the ``memory`` queue. One
user's turns must be ingested in order — turn N's collision checks depend on
turn N-1 being committed — and a single worker is global FIFO, which is that
guarantee with no locking. Scaling this queue past one worker silently breaks
ordering (the damage is bounded to spurious supersedes, because the gate
re-reads DB state inside each job — but bounded is not correct).

No automatic retry: retrying a failed turn after the NEXT turn has been
ingested would replay an old collision state. The transcript is already
persisted by the chat pipeline, so a failed extraction loses one extraction
and nothing else; it lands in RQ's failed registry and is logged loudly.
"""

import logging

from django_rq import job

from config.env import USE_POSTGRES
from conversations.constants import SenderType

logger = logging.getLogger(__name__)

MEMORY_QUEUE = "memory"


@job(MEMORY_QUEUE)
def run_memory_writer(ai_message_id: int) -> None:
    from conversations.models import Message
    from memory.models import MemoryLedgerEntry
    from memory.services.ingest import ingest_turn
    from memory.services.notify import announce_write, summarize_report

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
        # Anonymous/public-bot conversations have no one to remember for.
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

    # Idempotency: every write of a turn commits in one transaction with its
    # ledger rows, so a ledger row for this user message proves the turn is
    # already fully ingested.
    if MemoryLedgerEntry.objects.filter(source_message=user_message).exists():
        logger.info(
            "[memory] writer: message %s already ingested, skipping", user_message.id
        )
        return

    report = ingest_turn(user, conversation, user_message, ai_message)

    if report.skipped:
        logger.info("[memory] turn %s skipped: %s", user_message.id, report.skipped)
        return

    # The reply is long finished by now, so this arrives as a late note on a
    # closed turn rather than a step inside it. Recorded even when nothing was
    # stored: "considered and declined" is the honest answer, and it is the one
    # that makes the gate visible instead of merely trustworthy.
    #
    # Stored before it is sent, so a client that missed the event or reloads
    # the conversation still sees what happened.
    summary = summarize_report(report, report.entries)
    ai_message.memory_write_data = summary
    ai_message.save(update_fields=["memory_write_data", "updated_at"])
    announce_write(conversation.id, ai_message.id, summary)

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
