"""RQ worker defaults shared by document, memory and scheduler queues."""

import logging

from django.conf import settings
from django.db import connections
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
        finally:
            connections.close_all()
        return super().work(*args, **kwargs)

    def fork_work_horse(self, job, queue):
        # Parent-side ORM work must never leak a PostgreSQL/TLS socket into
        # successive children, even when a future startup hook opens one.
        connections.close_all()
        return super().fork_work_horse(job, queue)
