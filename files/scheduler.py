from datetime import timedelta

from django.utils import timezone
from django_rq import get_scheduler

from config import env
from core.services.ingestion_reconciliation import reconcile_interrupted_ingestions

# RQ lists a dead forking worker for up to 90 s (30 s heartbeat, 60 s grace),
# so a sweep queued after that window catches what a startup sweep cannot.
FOLLOW_UP_DELAY = timedelta(seconds=120)


def reconcile_interrupted_ingestions_job():
    summary = reconcile_interrupted_ingestions()
    return {
        "checked": summary.checked,
        "interrupted": summary.interrupted,
        "retained": summary.retained,
        "abandoned_attempts": summary.abandoned_attempts,
    }


class IngestionReconciliationScheduler:
    """Recurring sweep for files stranded in Processing by a dead worker.

    Registered by every worker at startup (idempotent: fixed job id), and run
    on the default queue so the ingestion workers execute it and no extra
    process is needed beyond the existing rq-scheduler.
    """

    def __init__(self, queue_name: str = "default"):
        self.scheduler = get_scheduler(queue_name)
        self.job_id = "ingestion_reconciliation"
        self.interval_seconds = env.INGESTION_RECONCILE_INTERVAL_SECONDS

    def start(self) -> dict:
        self.stop()
        if self.interval_seconds <= 0:
            return {"status": "disabled", "job_id": self.job_id}
        self.scheduler.enqueue_in(FOLLOW_UP_DELAY, reconcile_interrupted_ingestions_job)
        self.scheduler.schedule(
            scheduled_time=timezone.now(),
            func=reconcile_interrupted_ingestions_job,
            interval=self.interval_seconds,
            repeat=None,
            id=self.job_id,
            description="Mark interrupted document ingestions",
        )
        return {
            "status": "started",
            "job_id": self.job_id,
            "interval_seconds": self.interval_seconds,
        }

    def stop(self) -> dict:
        self.scheduler.cancel(self.job_id)
        return {"status": "stopped", "job_id": self.job_id}
