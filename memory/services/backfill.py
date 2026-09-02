"""Start and inspect user-requested memory builds from historical chats."""

import logging
from datetime import date
from typing import Tuple

from django.db import IntegrityError, transaction
from django.db.models import Max, QuerySet
from django.utils import timezone
from django_rq import get_queue

from conversations.constants import SenderType
from conversations.models import Message
from memory.constants import (
    ACTIVE_BACKFILL_STATUSES,
    MEMORY_QUEUE,
    MemoryBackfillStatus,
)
from memory.models import MemoryBackfillRun
from memory.services.api_service import MemoryUnavailable

logger = logging.getLogger(__name__)


def eligible_messages(run: MemoryBackfillRun) -> QuerySet:
    """Return the run's fixed, user-scoped set in conversation chronology."""
    messages = Message.active_objects.filter(
        conversation__user=run.user,
        sender_type=SenderType.AI_ASSISTANT,
        id__lte=run.cutoff_message_id,
        memory_write_data__isnull=True,
    ).exclude(message="")
    if run.since is not None:
        messages = messages.filter(created_at__date__gte=run.since)
    if run.until is not None:
        messages = messages.filter(created_at__date__lte=run.until)
    return messages.order_by("id")


def latest_run(user) -> MemoryBackfillRun | None:
    return (
        MemoryBackfillRun.active_objects.filter(user=user)
        .order_by("-created_at")
        .first()
    )


def start_run(
    user,
    *,
    since: date | None = None,
    until: date | None = None,
) -> Tuple[MemoryBackfillRun, bool]:
    """Create one immutable run, or return the user's in-flight run."""
    active = MemoryBackfillRun.active_objects.filter(
        user=user, status__in=ACTIVE_BACKFILL_STATUSES
    ).first()
    if active is not None:
        return active, False

    cutoff = (
        Message.active_objects.filter(
            conversation__user=user,
            sender_type=SenderType.AI_ASSISTANT,
        ).aggregate(latest=Max("id"))["latest"]
        or 0
    )

    try:
        with transaction.atomic():
            run = MemoryBackfillRun.objects.create(
                user=user,
                cutoff_message_id=cutoff,
                since=since,
                until=until,
            )
    except IntegrityError:
        return (
            MemoryBackfillRun.active_objects.get(
                user=user, status__in=ACTIVE_BACKFILL_STATUSES
            ),
            False,
        )

    total_turns = eligible_messages(run).count()
    if total_turns == 0:
        now = timezone.now()
        run.status = MemoryBackfillStatus.COMPLETED
        run.started_at = now
        run.completed_at = now
        run.save(update_fields=["status", "started_at", "completed_at", "updated_at"])
        return run, True

    run.total_turns = total_turns
    run.save(update_fields=["total_turns", "updated_at"])

    try:
        get_queue(MEMORY_QUEUE).enqueue(
            "memory.tasks.queue_memory_backfill",
            str(run.id),
        )
    except Exception as error:
        logger.exception(
            "[memory] failed to enqueue historical build for user %s", user.id
        )
        MemoryBackfillRun.objects.filter(pk=run.id).update(
            status=MemoryBackfillStatus.FAILED,
            completed_at=timezone.now(),
            error_message="Memory building could not be started. Try again.",
        )
        raise MemoryUnavailable(
            "Memory building could not be started. Try again."
        ) from error

    return run, True


def stop_run(user) -> MemoryBackfillRun | None:
    """Cooperatively stop a build; queued turn jobs become harmless no-ops."""
    with transaction.atomic():
        run = (
            MemoryBackfillRun.active_objects.select_for_update()
            .filter(user=user, status__in=ACTIVE_BACKFILL_STATUSES)
            .order_by("-created_at")
            .first()
        )
        if run is None:
            return latest_run(user)

        run.status = MemoryBackfillStatus.STOPPED
        run.completed_at = timezone.now()
        run.error_message = ""
        run.save(
            update_fields=[
                "status",
                "completed_at",
                "error_message",
                "updated_at",
            ]
        )
        return run
