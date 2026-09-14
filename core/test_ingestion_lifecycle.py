"""Cancellation and publication races, using real database transactions."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from core.services.document_processor import DocumentProcessor
from core.services.ingestion_lifecycle import IngestionCancelled, persist_ingestion_file
from core.test_document_ingestion_map import (
    FLAT_PARSED,
    fake_embeddings,
    patched_ingestion,
)
from files.models import DocumentChunk, File, VectorIndexAttempt
from files.tasks import delete_file_vectors


class IngestionLifecycleTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="lifecycle@example.com", password="pw"
        )
        self.cleanup = patch("files.signals.delete_file_vectors.delay")
        self.enqueue_cleanup = self.cleanup.start()
        self.addCleanup(self.cleanup.stop)
        self.file = self.make_file()

    def make_file(self):
        return File.active_objects.create(
            user=self.user,
            name="lifecycle.txt",
            file="",
            file_type="text/plain",
            processing_mode="basic",
        )

    def delete(self, file_id):
        File._base_manager.filter(pk=file_id).delete()

    def test_deleted_before_job_starts_is_noop(self):
        file_id = self.file.pk
        self.delete(file_id)
        with patched_ingestion(), patch.object(
            DocumentProcessor, "create_file_embeddings"
        ) as process:
            self.assertIsNone(
                DocumentIngestionService().process(DocumentIngestionCommand(file_id))
            )
        process.assert_not_called()

    def test_delete_during_parse_does_not_save_failure_or_recreate_file(self):
        file_id = self.file.pk

        def parse(file):
            self.delete(file.pk)
            raise FileNotFoundError("Deleted while parsing")

        with patched_ingestion(), patch.object(
            DocumentProcessor, "parse_file", side_effect=parse
        ):
            self.assertIsNone(
                DocumentIngestionService().process(DocumentIngestionCommand(file_id))
            )
        self.assertFalse(File._base_manager.filter(pk=file_id).exists())

    def test_atomic_persistence_rejects_deleted_and_replaced_attempts(self):
        self.file.extracted_text = "late result"
        File._base_manager.filter(pk=self.file.pk).update(ingestion_token=uuid4())
        with self.assertRaises(IngestionCancelled):
            persist_ingestion_file(self.file, ["extracted_text"])
        self.file.refresh_from_db()
        self.assertIsNone(self.file.extracted_text)
        self.delete(self.file.pk)
        with self.assertRaises(IngestionCancelled):
            persist_ingestion_file(self.file, ["extracted_text"])

    def test_delete_can_commit_while_embeddings_are_running(self):
        file_id = self.file.pk

        def embeddings(*args):
            self.assertFalse(connection.in_atomic_block)

            def delete_from_other_connection():
                try:
                    self.delete(file_id)
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(delete_from_other_connection).result(timeout=10)
            return fake_embeddings(*args)

        with patched_ingestion(embed_side_effect=embeddings):
            self.assertIsNone(
                DocumentIngestionService().process(DocumentIngestionCommand(file_id))
            )
        self.assertFalse(File._base_manager.filter(pk=file_id).exists())
        self.assertFalse(DocumentChunk.objects.filter(file_id=file_id).exists())

    def test_delete_during_vector_write_cleans_staging_without_publication(self):
        service = Mock()
        file_id = self.file.pk

        def store(vectors, user_id, generation):
            self.assertFalse(connection.in_atomic_block)
            self.delete(file_id)
            return len(vectors)

        with patched_ingestion(
            extra_patchers=[
                patch.object(DocumentProcessor, "_store_vectors", side_effect=store),
            ]
        ), patch.object(
            DocumentProcessor,
            "update_vector_service",
            lambda processor, _, **kwargs: setattr(
                processor, "vector_service", service
            ),
        ):
            self.assertIsNone(
                DocumentIngestionService().process(DocumentIngestionCommand(file_id))
            )
        self.assertFalse(File._base_manager.filter(pk=file_id).exists())
        service.delete_file_vectors.assert_called_once()
        self.assertNotEqual(service.delete_file_vectors.call_args.args[0], str(file_id))
        service.close.assert_called_once()

    def test_replaced_attempt_cannot_publish_or_clear_new_lease(self):
        new_token = uuid4()

        def embeddings(*args):
            File._base_manager.filter(pk=self.file.pk).update(ingestion_token=new_token)
            return fake_embeddings(*args)

        with patched_ingestion(embed_side_effect=embeddings):
            self.assertIsNone(
                DocumentIngestionService().process(
                    DocumentIngestionCommand(self.file.pk)
                )
            )
        self.file.refresh_from_db()
        self.assertEqual(self.file.ingestion_token, new_token)
        self.assertFalse(self.file.index_generation)
        self.assertEqual(
            VectorIndexAttempt.objects.get(file=self.file).status, "abandoned"
        )

    def test_deletion_snapshots_generations_and_waits_for_commit(self):
        File._base_manager.filter(pk=self.file.pk).update(
            index_generation="published", vector_db_source=1
        )
        VectorIndexAttempt.objects.create(
            file=self.file, generation="staging", owner_id=self.user.pk, backend=2
        )
        with transaction.atomic():
            self.delete(self.file.pk)
            self.enqueue_cleanup.assert_not_called()
        self.enqueue_cleanup.assert_called_once_with(
            self.file.pk, self.user.pk, [("staging", 2), ("published", 1)]
        )

    def test_rolled_back_deletion_does_not_enqueue_cleanup(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.delete(self.file.pk)
                raise RuntimeError("rollback")
        self.enqueue_cleanup.assert_not_called()
        self.assertTrue(File._base_manager.filter(pk=self.file.pk).exists())

    def test_cleanup_uses_snapshot_after_file_is_gone_and_is_retryable(self):
        self.delete(self.file.pk)
        service = Mock()
        with patch(
            "files.tasks.get_vector_service", return_value=service
        ) as get_service:
            for _ in range(2):
                delete_file_vectors(self.file.pk, self.user.pk, [("generation", 2)])
            self.assertEqual(service.delete_file_vectors.call_count, 2)
            get_service.assert_called_with(self.user.pk, backend=2)
            service.delete_file_vectors.return_value = False
            with self.assertRaises(RuntimeError):
                delete_file_vectors(self.file.pk, self.user.pk, [("generation", 2)])

    def test_seventeen_uploads_with_concurrent_cancellation(self):
        files = [self.file] + [self.make_file() for _ in range(16)]
        cancelled = {file.pk for file in files[::3]}

        def embeddings(*args):
            if args[1] in cancelled:
                self.delete(args[1])
            return fake_embeddings(*args)

        def run(file_id):
            try:
                return DocumentIngestionService().process(
                    DocumentIngestionCommand(file_id)
                )
            finally:
                connections.close_all()

        with patched_ingestion(embed_side_effect=embeddings), ThreadPoolExecutor(
            max_workers=4
        ) as executor:
            results = list(executor.map(run, [file.pk for file in files]))
        for file, result in zip(files, results):
            if file.pk in cancelled:
                self.assertIsNone(result)
                self.assertFalse(File._base_manager.filter(pk=file.pk).exists())
            else:
                self.assertGreater(result, 0)
                file.refresh_from_db()
                self.assertIsNone(file.ingestion_token)
                self.assertEqual(
                    VectorIndexAttempt.objects.get(file=file).status, "published"
                )
                self.assertEqual(
                    DocumentChunk.objects.filter(file=file).count(), result
                )
