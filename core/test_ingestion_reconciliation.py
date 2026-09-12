from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from core.services.file_processing_journey import FileProcessingJourney
from core.services.ingestion_reconciliation import (
    INTERRUPTED_MESSAGE,
    RETAINED_MESSAGE,
    reconcile_interrupted_ingestions,
)
from files.constants import FileProcessingStage, FileStatus
from files.models import File, VectorIndexAttempt


def fake_job(job_id, state):
    return SimpleNamespace(
        id=job_id,
        is_queued=state == "queued",
        is_deferred=state == "deferred",
        is_scheduled=state == "scheduled",
        is_started=state == "started",
        is_finished=state == "finished",
        is_failed=state == "failed",
    )


class IngestionReconciliationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="reconcile@example.com", password="pw"
        )
        self.jobs = {}
        self.queue = MagicMock()
        self.queue.fetch_job.side_effect = lambda job_id: self.jobs.get(job_id)
        self.running = set()
        patcher = patch(
            "core.services.ingestion_reconciliation._running_job_ids",
            side_effect=lambda queue: self.running,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_file(self, job_id, *, age=timedelta(0), **fields):
        file = File.active_objects.create(
            user=self.user,
            name=f"{job_id}.pdf",
            file=SimpleUploadedFile("f.pdf", b"%PDF-test"),
            job_id=job_id,
            status=FileStatus.PROCESSING,
            processing_stage=FileProcessingStage.EMBEDDING,
            **fields,
        )
        journey = FileProcessingJourney(file)
        journey.begin_attempt()
        if age:
            File.active_objects.filter(pk=file.pk).update(
                updated_at=timezone.now() - age
            )
        return file

    def reconcile(self):
        return reconcile_interrupted_ingestions(queue=self.queue)

    def test_live_jobs_are_left_alone(self):
        queued = self.make_file("q")
        self.jobs["q"] = fake_job("q", "queued")
        started = self.make_file("s")
        self.jobs["s"] = fake_job("s", "started")
        self.running.add("s")
        fresh = self.make_file("none")
        just_finished = self.make_file("f")
        self.jobs["f"] = fake_job("f", "finished")

        summary = self.reconcile()

        self.assertEqual(summary.checked, 4)
        self.assertEqual(summary.interrupted, [])
        for file in (queued, started, fresh, just_finished):
            file.refresh_from_db()
            self.assertEqual(file.status, FileStatus.PROCESSING)

    def test_started_job_without_a_live_worker_is_interrupted(self):
        file = self.make_file("dead")
        self.jobs["dead"] = fake_job("dead", "started")
        VectorIndexAttempt.objects.create(
            file=file, generation="staged", owner_id=self.user.pk, backend=1
        )

        summary = self.reconcile()

        self.assertEqual(summary.interrupted, [file.pk])
        self.assertEqual(summary.abandoned_attempts, 1)
        file.refresh_from_db()
        self.assertEqual(file.status, FileStatus.FAILED)
        self.assertEqual(file.processing_stage, FileProcessingStage.COMPLETE)
        self.assertEqual(file.error_message, INTERRUPTED_MESSAGE)
        self.assertIsNone(file.ingestion_token)
        attempt = file.processing_journey["attempts"][-1]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["error"], INTERRUPTED_MESSAGE)
        row = VectorIndexAttempt.objects.get(generation="staged")
        self.assertEqual(row.status, "abandoned")
        self.assertIsNotNone(row.finished_at)

    def test_interrupted_replacement_keeps_the_previous_index(self):
        file = self.make_file("dead", index_generation="live")
        self.jobs["dead"] = fake_job("dead", "failed")
        VectorIndexAttempt.objects.create(
            file=file,
            generation="live",
            owner_id=self.user.pk,
            backend=1,
            status="published",
        )

        summary = self.reconcile()

        self.assertEqual(summary.retained, [file.pk])
        file.refresh_from_db()
        self.assertEqual(file.status, FileStatus.PROCESSED)
        self.assertEqual(file.error_message, RETAINED_MESSAGE)
        self.assertEqual(file.index_generation, "live")
        self.assertEqual(
            VectorIndexAttempt.objects.get(generation="live").status, "published"
        )

    def test_untracked_and_stale_finished_jobs_expire_after_the_grace_period(self):
        untracked = self.make_file(None, age=timedelta(minutes=6))
        finished = self.make_file("f", age=timedelta(minutes=6))
        self.jobs["f"] = fake_job("f", "finished")

        summary = self.reconcile()

        self.assertEqual(
            sorted(summary.interrupted), sorted([untracked.pk, finished.pk])
        )

    def test_processed_files_are_never_candidates(self):
        file = self.make_file("done")
        File.active_objects.filter(pk=file.pk).update(status=FileStatus.PROCESSED)

        summary = self.reconcile()

        self.assertEqual(summary.checked, 0)
