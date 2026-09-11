import logging

from django.utils import timezone
from django_rq import get_scheduler

from config import env

logger = logging.getLogger(__name__)


def reconcile_interrupted_ingestions_job():
    from core.services.ingestion_reconciliation import reconcile_interrupted_ingestions

    summary = reconcile_interrupted_ingestions()
    return {
        "checked": summary.checked,
        "interrupted": summary.interrupted,
        "retained": summary.retained,
        "abandoned_attempts": summary.abandoned_attempts,
    }


class IngestionReconciliationScheduler:
    """Recurring sweep for files stranded in Processing by a dead worker.

    Runs on the default queue so the same workers that ingest documents also
    reconcile them; no extra queue needs a listener.
    """

    def __init__(self, queue_name: str = "default"):
        self.scheduler = get_scheduler(queue_name)
        self.job_id = "ingestion_reconciliation"
        self.interval_seconds = env.INGESTION_RECONCILE_INTERVAL_SECONDS

    def start(self) -> dict:
        self.stop()
        if self.interval_seconds <= 0:
            logger.info("Ingestion reconciliation sweep is disabled.")
            return {"status": "disabled", "job_id": self.job_id}
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
        try:
            self.scheduler.cancel(self.job_id)
        except Exception:
            pass
        return {"status": "stopped", "job_id": self.job_id}
