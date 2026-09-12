"""Reconcile files left in Processing by a worker that stopped mid-job.

A file only leaves Processing when its job finishes, so a killed worker
leaves it there forever: the lease in ``DocumentIngestionService`` is only
reclaimed by the next explicit reprocess. This sweep asks RQ whether the job
is still alive and, when it is not, records the interruption honestly:
Failed when the file never had a working index, Processed (with a note) when
a replacement attempt was interrupted and the previous index is still active.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

from django.db import transaction
from django.utils import timezone
from django_rq import get_queue
from rq import Worker

from core.services.file_processing_journey import FileProcessingJourney
from files.constants import FileProcessingStage, FileStatus
from files.models import File, VectorIndexAttempt

logger = logging.getLogger(__name__)

# A freshly uploaded file can sit briefly with no job or a not-yet-started job
# while the upload transaction commits; older than this with no live job is dead.
UNTRACKED_GRACE = timedelta(minutes=5)

INTERRUPTED_MESSAGE = (
    "Processing was interrupted before it finished because the background "
    "worker stopped. Reprocess the file to try again."
)
RETAINED_MESSAGE = (
    "Reprocessing was interrupted before it finished; the previous document "
    "and search index were retained."
)
ABANDONED_ATTEMPT_ERROR = "Worker stopped before this attempt finished."


@dataclass
class ReconciliationSummary:
    checked: int = 0
    interrupted: List[int] = field(default_factory=list)
    retained: List[int] = field(default_factory=list)
    abandoned_attempts: int = 0


def _running_job_ids(queue) -> set:
    """Job ids a live worker (one still heartbeating) says it is executing."""
    running = set()
    for worker in Worker.all(connection=queue.connection):
        job_id = worker.get_current_job_id()
        if job_id:
            running.add(job_id)
    return running


def _job_is_alive(file: File, job, running_job_ids: set, now) -> bool:
    if job is None:
        return file.updated_at >= now - UNTRACKED_GRACE
    if job.is_queued or job.is_deferred or job.is_scheduled:
        return True
    if job.is_started:
        return job.id in running_job_ids
    if job.is_finished:
        # The job returned but never finalized the file; give the finalizing
        # write a moment, then treat it as interrupted.
        return file.updated_at >= now - UNTRACKED_GRACE
    return False


def reconcile_interrupted_ingestions(
    now=None, queue=None, file_ids: Optional[List[int]] = None
) -> ReconciliationSummary:
    now = now or timezone.now()
    queue = queue or get_queue()
    running = _running_job_ids(queue)
    summary = ReconciliationSummary()

    candidates = File.active_objects.filter(
        status=FileStatus.PROCESSING, is_media=False
    )
    if file_ids is not None:
        candidates = candidates.filter(pk__in=file_ids)
    for file in candidates.only(
        "id", "job_id", "updated_at", "status", "index_generation"
    ):
        summary.checked += 1
        job = queue.fetch_job(file.job_id) if file.job_id else None
        if _job_is_alive(file, job, running, now):
            continue
        result = _mark_interrupted(file.pk, now)
        if result is None:
            continue
        retained, abandoned = result
        (summary.retained if retained else summary.interrupted).append(file.pk)
        summary.abandoned_attempts += abandoned
    return summary


def _mark_interrupted(file_id: int, now):
    with transaction.atomic():
        file = (
            File.active_objects.select_for_update()
            .filter(pk=file_id, status=FileStatus.PROCESSING)
            .first()
        )
        if file is None:
            return None
        retained = (
            bool(file.index_generation)
            and VectorIndexAttempt.objects.filter(
                file=file, generation=file.index_generation, status="published"
            ).exists()
        )
        journey = FileProcessingJourney(file)
        if (journey.current_attempt or {}).get("status") == "processing":
            journey.fail_attempt(INTERRUPTED_MESSAGE)
        file.status = FileStatus.PROCESSED if retained else FileStatus.FAILED
        file.processing_stage = FileProcessingStage.COMPLETE
        file.error_message = RETAINED_MESSAGE if retained else INTERRUPTED_MESSAGE
        file.ingestion_token = None
        file.ingestion_started_at = None
        file.save(
            update_fields=[
                "status",
                "processing_stage",
                "error_message",
                "ingestion_token",
                "ingestion_started_at",
            ]
        )
        abandoned = VectorIndexAttempt.objects.filter(
            file=file, status="running"
        ).update(status="abandoned", finished_at=now, error=ABANDONED_ATTEMPT_ERROR)
    logger.warning(
        "Document ingestion interrupted for file %s (previous index %s)",
        file_id,
        "retained" if retained else "absent",
    )
    return retained, abandoned
