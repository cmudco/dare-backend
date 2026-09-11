from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from files.scheduler import (
    FOLLOW_UP_DELAY,
    IngestionReconciliationScheduler,
    reconcile_interrupted_ingestions_job,
)


class IngestionReconciliationSchedulerTests(SimpleTestCase):
    def make(self, interval):
        scheduler = MagicMock()
        with patch("files.scheduler.get_scheduler", return_value=scheduler), patch(
            "files.scheduler.env.INGESTION_RECONCILE_INTERVAL_SECONDS", interval
        ):
            registrar = IngestionReconciliationScheduler()
        return registrar, scheduler

    def test_start_replaces_the_schedule_and_queues_a_follow_up(self):
        registrar, scheduler = self.make(300)

        result = registrar.start()

        scheduler.cancel.assert_called_once_with("ingestion_reconciliation")
        scheduler.enqueue_in.assert_called_once_with(
            FOLLOW_UP_DELAY, reconcile_interrupted_ingestions_job
        )
        schedule = scheduler.schedule.call_args.kwargs
        self.assertEqual(schedule["id"], "ingestion_reconciliation")
        self.assertEqual(schedule["interval"], 300)
        self.assertIs(schedule["func"], reconcile_interrupted_ingestions_job)
        self.assertEqual(result["status"], "started")

    def test_zero_interval_only_cancels(self):
        registrar, scheduler = self.make(0)

        result = registrar.start()

        scheduler.cancel.assert_called_once()
        scheduler.enqueue_in.assert_not_called()
        scheduler.schedule.assert_not_called()
        self.assertEqual(result["status"], "disabled")
