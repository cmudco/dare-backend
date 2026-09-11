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
