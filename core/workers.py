"""RQ worker defaults shared by document, memory and scheduler queues."""

import logging

from django.conf import settings
from rq import Worker

from core.services.ingestion_reconciliation import reconcile_interrupted_ingestions
from files.scheduler import IngestionReconciliationScheduler

logger = logging.getLogger(__name__)


class InspectableWorker(Worker):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default_result_ttl", settings.RQ["DEFAULT_RESULT_TTL"])
        super().__init__(*args, **kwargs)

    def work(self, *args, **kwargs):
        # A restarted worker is the first to know its predecessor's jobs are
        # dead, so sweep before taking new work. RQ still lists a dead worker
        # until its heartbeat expires, so the scheduler also gets a one-off
        # follow-up sweep after that window plus the recurring schedule.
        try:
            summary = reconcile_interrupted_ingestions()
            if summary.interrupted or summary.retained:
                logger.warning(
                    "Startup sweep marked interrupted ingestions: failed=%s retained=%s",
                    summary.interrupted,
                    summary.retained,
                )
            logger.info(
                "Ingestion reconciliation schedule: %s",
                IngestionReconciliationScheduler().start(),
            )
        except Exception:
            logger.exception("Startup ingestion sweep failed; worker continues")
        return super().work(*args, **kwargs)
