import logging
from datetime import timedelta

from config import env
from django.utils import timezone
from django_rq import get_scheduler

from syftbox.tasks import sync_syftbox_datasites

logger = logging.getLogger(__name__)


class SyftBoxDatasiteScheduler:
    """Recurring scheduler for SyftBox datasite sync jobs."""

    def __init__(self, queue_name: str = "scheduler"):
        self.scheduler = get_scheduler(queue_name)
        self.job_id = "syftbox_datasite_sync"
        self.interval_seconds = env.SYFTBOX_SYNC_INTERVAL_SECONDS

    def start(self) -> dict:
        try:
            self.stop()
            self.scheduler.schedule(
                scheduled_time=timezone.now(),
                func=sync_syftbox_datasites,
                interval=self.interval_seconds,
                repeat=None,
                id=self.job_id,
                description="Recurring SyftBox datasite sync dispatcher",
                meta={
                    "created_at": timezone.now().isoformat(),
                    "interval_seconds": self.interval_seconds,
                    "scheduler_version": "1.0",
                },
            )
            return {
                "status": "started",
                "job_id": self.job_id,
                "interval_seconds": self.interval_seconds,
            }
        except Exception as error:
            logger.error("Failed to start SyftBox scheduler: %s", error)
            return {"status": "error", "message": str(error)}

    def stop(self) -> dict:
        try:
            self.scheduler.cancel(self.job_id)
            return {"status": "stopped", "job_id": self.job_id}
        except Exception as error:
            logger.info("No SyftBox scheduler job to cancel: %s", error)
            return {"status": "not_found", "message": str(error)}

    def status(self) -> dict:
        try:
            scheduled_jobs = list(self.scheduler.get_jobs())
            for job in scheduled_jobs:
                if job.id == self.job_id:
                    return {
                        "status": "active",
                        "job_id": job.id,
                        "description": job.description,
                        "next_run": getattr(job, "scheduled_for", "Unknown"),
                        "created_at": job.meta.get("created_at", "Unknown"),
                        "interval_seconds": job.meta.get(
                            "interval_seconds",
                            self.interval_seconds,
                        ),
                    }
            return {"status": "inactive", "job_id": self.job_id}
        except Exception as error:
            logger.error("Failed to read SyftBox scheduler status: %s", error)
            return {"status": "error", "message": str(error)}

    def restart(self) -> dict:
        return {
            "status": "restarted",
            "stop_result": self.stop(),
            "start_result": self.start(),
        }

    def run_now(self, delay_seconds: int = 5) -> dict:
        try:
            test_job_id = (
                f"{self.job_id}_test_{timezone.now().strftime('%Y%m%d_%H%M%S')}"
            )
            run_time = timezone.now() + timedelta(seconds=delay_seconds)
            self.scheduler.schedule(
                scheduled_time=run_time,
                func=sync_syftbox_datasites,
                interval=None,
                id=test_job_id,
                description="One-time SyftBox datasite sync dispatcher test",
            )
            return {
                "status": "scheduled",
                "test_job_id": test_job_id,
                "scheduled_for": run_time.isoformat(),
                "delay_seconds": delay_seconds,
            }
        except Exception as error:
            logger.error("Failed to schedule one-time SyftBox run: %s", error)
            return {"status": "error", "message": str(error)}


def start_scheduler() -> dict:
    return SyftBoxDatasiteScheduler().start()


def stop_scheduler() -> dict:
    return SyftBoxDatasiteScheduler().stop()


def get_scheduler_status() -> dict:
    return SyftBoxDatasiteScheduler().status()


def restart_scheduler() -> dict:
    return SyftBoxDatasiteScheduler().restart()


def run_sync_now(delay_seconds: int = 5) -> dict:
    return SyftBoxDatasiteScheduler().run_now(delay_seconds)
