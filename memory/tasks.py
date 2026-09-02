"""Post-reply ingestion on the dedicated memory queue."""

import logging

from django.db import close_old_connections, transaction
from django.utils import timezone
from django_rq import job

from config.env import USE_POSTGRES
from conversations.constants import SenderType
from conversations.models import Message
from memory.constants import (
    ACTIVE_BACKFILL_STATUSES,
    MEMORY_QUEUE,
    MemoryBackfillStatus,
)
from memory.models import MemoryBackfillRun, MemoryLedgerEntry
from memory.services.backfill import eligible_messages
from memory.services.ingest import ingest_turn
from memory.services.notify import announce_write, summarize_report

logger = logging.getLogger(__name__)


@job(MEMORY_QUEUE)
def run_memory_writer(ai_message_id: int, backfill_run_id: str | None = None) -> None:
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

    if (
        backfill_run_id is None
        and MemoryBackfillRun.active_objects.filter(
            user=user,
            status__in=ACTIVE_BACKFILL_STATUSES,
        ).exists()
    ):
        # Historical turns own this user's write order until their fixed
        # snapshot is complete. Put a new live turn behind them in the same
        # serialized queue rather than letting old facts overwrite new ones.
        run_memory_writer.delay(ai_message_id)
        logger.info(
            "[memory] deferred live message %s behind active historical build",
            ai_message_id,
        )
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

    if ai_message.memory_write_data is not None:
        logger.info(
            "[memory] writer: message %s already ingested, skipping", user_message.id
        )
        return

    if MemoryLedgerEntry.objects.filter(source_message=user_message).exists():
        # A retry may find the ledger commit after the reply marker was lost,
        # or another assistant reply may point at the same user turn. Restore
        # the marker so later backfill runs do not rediscover it forever.
        ai_message.memory_write_data = {
            "created": 0,
            "retired": 0,
            "reinforced": 0,
            "profileChanged": False,
            "considered": 0,
            "changes": [],
            "skipped": "turn already ingested",
        }
        ai_message.save(update_fields=["memory_write_data", "updated_at"])
        logger.info(
            "[memory] writer: restored marker for ingested message %s",
            user_message.id,
        )
        return

    report = ingest_turn(user, conversation, user_message, ai_message)

    if report.skipped:
        ai_message.memory_write_data = {
            **summarize_report(report, report.entries),
            "skipped": report.skipped,
        }
        ai_message.save(update_fields=["memory_write_data", "updated_at"])
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
        "profile_changed=%s",
        user_message.id,
        report.decisions,
        len(report.created),
        report.retired,
        report.reinforced,
        report.profile_changed,
    )


@job(MEMORY_QUEUE)
def queue_memory_backfill(run_id: str) -> None:
    """Freeze and enqueue one ordered writer job for every eligible old reply."""
    close_old_connections()
    run = (
        MemoryBackfillRun.active_objects.select_related("user")
        .filter(pk=run_id, status=MemoryBackfillStatus.QUEUED)
        .first()
    )
    if run is None:
        return

    message_ids = list(eligible_messages(run).values_list("id", flat=True))
    now = timezone.now()
    MemoryBackfillRun.objects.filter(pk=run.id).update(
        status=(
            MemoryBackfillStatus.RUNNING
            if message_ids
            else MemoryBackfillStatus.COMPLETED
        ),
        total_turns=len(message_ids),
        started_at=now,
        completed_at=None if message_ids else now,
    )

    try:
        for message_id in message_ids:
            run_memory_backfill_turn.delay(str(run.id), message_id)
    except Exception as error:
        _fail_backfill(run.id)
        logger.exception("[memory] failed while queueing historical build %s", run.id)
        raise error


@job(MEMORY_QUEUE)
def run_memory_backfill_turn(run_id: str, ai_message_id: int) -> None:
    """Process one historical reply and advance its persisted run."""
    close_old_connections()
    run = MemoryBackfillRun.active_objects.filter(
        pk=run_id,
        status=MemoryBackfillStatus.RUNNING,
    ).first()
    if run is None:
        return

    try:
        run_memory_writer(ai_message_id, backfill_run_id=run_id)
    except Exception:
        _fail_backfill(run.id)
        logger.exception(
            "[memory] historical build %s failed on message %s",
            run.id,
            ai_message_id,
        )
        raise

    with transaction.atomic():
        locked = MemoryBackfillRun.objects.select_for_update().get(pk=run.id)
        if locked.status != MemoryBackfillStatus.RUNNING:
            return
        locked.processed_turns += 1
        update_fields = ["processed_turns", "updated_at"]
        if locked.processed_turns >= locked.total_turns:
            locked.status = MemoryBackfillStatus.COMPLETED
            locked.completed_at = timezone.now()
            update_fields.extend(["status", "completed_at"])
        locked.save(update_fields=update_fields)


def _fail_backfill(run_id) -> None:
    MemoryBackfillRun.objects.filter(
        pk=run_id,
        status__in=ACTIVE_BACKFILL_STATUSES,
    ).update(
        status=MemoryBackfillStatus.FAILED,
        completed_at=timezone.now(),
        error_message="Memory building stopped before it finished. Try again.",
    )
