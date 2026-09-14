from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase

from billing.constants import UserWalletPreferenceTypeChoice
from core.services.document_enrichment_service import (
    DocumentEnrichmentService,
    VisionOperation,
)
from core.services.ingestion_lifecycle import IngestionCancelled
from files.models import File


class VisionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="vision-concurrency@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user, name="vision.pdf", file="", ingestion_token=uuid4()
        )
        self.route = SimpleNamespace(
            wallet_type="LITELLM", model=SimpleNamespace(identifier="test")
        )
        self.operations = [VisionOperation("page_transcription", n) for n in range(8)]
        self.service = DocumentEnrichmentService()

    def test_parallel_operations_have_isolated_state_and_bounded_concurrency(self):
        barrier, lock = Barrier(2), Lock()
        active, peak = 0, 0
        file_objects = []

        def transcribe(file, page, route, service, telemetry):
            nonlocal active, peak
            self.assertFalse(connection.in_atomic_block)
            with lock:
                active += 1
                peak = max(peak, active)
                file_objects.append(file)
            barrier.wait(timeout=10)
            telemetry.provider_requests += 1
            with lock:
                active -= 1
            return {"status": "complete", "page": page}

        with patch(
            "core.services.document_enrichment_service.env.DOCUMENT_ENRICHMENT_CONCURRENCY",
            2,
        ), patch.object(
            self.service, "_build_ai_service", side_effect=lambda *args: Mock()
        ) as build, patch.object(
            self.service, "_transcribe_page", side_effect=transcribe
        ):
            results = list(
                self.service._run_operations(
                    self.operations, self.file, [], self.route, None, None
                )
            )
        self.assertEqual(peak, 2)
        self.assertEqual(build.call_count, 8)
        self.assertEqual(len({id(file) for file in file_objects}), 8)
        self.assertEqual([row[1]["page"] for row in results], list(range(8)))
        self.assertEqual(sum(row[2].provider_requests for row in results), 8)

    def test_replaced_lease_stops_submission_after_current_batch(self):
        def transcribe(*args):
            File._base_manager.filter(pk=self.file.pk).update(ingestion_token=uuid4())
            return {"status": "complete"}

        with patch(
            "core.services.document_enrichment_service.env.DOCUMENT_ENRICHMENT_CONCURRENCY",
            2,
        ), patch.object(
            self.service, "_build_ai_service", return_value=Mock()
        ), patch.object(
            self.service, "_transcribe_page", side_effect=transcribe
        ) as calls:
            with self.assertRaises(IngestionCancelled):
                list(
                    self.service._run_operations(
                        self.operations, self.file, [], self.route, None, None
                    )
                )
        self.assertLessEqual(calls.call_count, 2)

    def test_platform_wallet_stays_on_serial_credit_check_lane(self):
        self.route.wallet_type = UserWalletPreferenceTypeChoice.DARE
        with patch(
            "core.services.document_enrichment_service.ThreadPoolExecutor"
        ) as pool, patch.object(
            self.service, "_transcribe_page", return_value={"status": "complete"}
        ) as transcribe:
            results = list(
                self.service._run_operations(
                    self.operations, self.file, [], self.route, None, None
                )
            )
        pool.assert_not_called()
        self.assertEqual(transcribe.call_count, 8)
        self.assertEqual(len(results), 8)

    def test_one_failed_operation_preserves_other_results(self):
        def transcribe(file, page, *args):
            if page == 3:
                raise RuntimeError("synthetic provider failure")
            return {"status": "complete"}

        with patch(
            "core.services.document_enrichment_service.env.DOCUMENT_ENRICHMENT_CONCURRENCY",
            2,
        ), patch.object(
            self.service, "_build_ai_service", return_value=Mock()
        ), patch.object(
            self.service, "_transcribe_page", side_effect=transcribe
        ):
            results = list(
                self.service._run_operations(
                    self.operations, self.file, [], self.route, None, None
                )
            )
        self.assertEqual(sum(row[2].failed_operations for row in results), 1)
        self.assertEqual(sum(row[1]["status"] == "complete" for row in results), 7)
