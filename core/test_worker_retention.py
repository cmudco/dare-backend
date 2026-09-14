from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django_rq.workers import get_worker_class
from rq import Worker

from core.workers import InspectableWorker


class WorkerRetentionTests(SimpleTestCase):
    def test_all_queues_use_retaining_worker(self):
        self.assertIs(get_worker_class(), InspectableWorker)
        for queue in ("default", "memory", "scheduler"):
            with self.subTest(queue=queue), patch.object(
                Worker, "__init__", return_value=None
            ) as init:
                InspectableWorker([queue])
                self.assertEqual(init.call_args.kwargs["default_result_ttl"], 604800)

    def test_explicit_worker_override_is_preserved(self):
        with patch.object(Worker, "__init__", return_value=None) as init:
            InspectableWorker([], default_result_ttl=120)
        self.assertEqual(init.call_args.kwargs["default_result_ttl"], 120)


class WorkerConnectionTests(SimpleTestCase):
    def test_startup_closes_connections_before_work_even_when_sweep_fails(self):
        for error in (None, RuntimeError("sweep failed")):
            events = []
            worker = object.__new__(InspectableWorker)
            with self.subTest(error=error), patch(
                "core.workers.reconcile_interrupted_ingestions",
                return_value=SimpleNamespace(interrupted=[], retained=[]),
                side_effect=error,
            ), patch("core.workers.IngestionReconciliationScheduler"), patch(
                "core.workers.connections.close_all",
                side_effect=lambda: events.append("close"),
            ), patch.object(
                Worker, "work", side_effect=lambda: events.append("work")
            ):
                worker.work()
            self.assertEqual(events, ["close", "work"])

    def test_every_fork_closes_parent_connections(self):
        worker = object.__new__(InspectableWorker)
        events = []
        with patch(
            "core.workers.connections.close_all",
            side_effect=lambda: events.append("close"),
        ), patch.object(
            Worker, "fork_work_horse", side_effect=lambda *args: events.append("fork")
        ):
            for _ in range(20):
                worker.fork_work_horse(None, None)
        self.assertEqual(events, ["close", "fork"] * 20)
