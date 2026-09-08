from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from files.constants import (
    DocumentProcessingMode,
    DocumentReprocessingAction,
    FileStatus,
)
from files.models import File


class DocumentReprocessingTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="reprocess@example.com", password="test"
        )
        self.client.force_authenticate(self.user)
        self.file = File.active_objects.create(
            user=self.user,
            name="notes.txt",
            file="files/notes.txt",
            status=FileStatus.PROCESSED,
            processing_mode=DocumentProcessingMode.BASIC,
            parser_name="basic",
            extracted_text="Original text",
            document_model={"parser": "basic"},
            index_generation="original-index",
        )
        self.url = f"/api/files/{self.file.pk}/reprocess/"

    @patch("files.services.document_reprocessing_service.enqueue")
    @patch("files.services.document_reprocessing_service.select_vision_model")
    def test_selected_vision_model_is_queued_without_changing_default(
        self, select_model, enqueue
    ):
        before = self.user.vision_model
        response = self.client.post(
            self.url,
            {
                "action": "reparse",
                "processingMode": "advanced",
                "modelIdentifier": "alternate-vision",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 202, response.data)
        select_model.assert_called_once_with(self.user, "alternate-vision")
        self.assertEqual(
            enqueue.call_args.kwargs["model_identifier"], "alternate-vision"
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.vision_model, before)

    @patch("files.services.document_reprocessing_service.enqueue")
    @patch("files.services.document_reprocessing_service.select_vision_model")
    def test_unavailable_model_does_not_queue_or_change_file(
        self, select_model, enqueue
    ):
        from core.services.vision_model_service import VisionModelNotOffered

        select_model.side_effect = VisionModelNotOffered("Model is unavailable")
        response = self.client.post(
            self.url,
            {
                "action": "reparse",
                "processingMode": "advanced",
                "modelIdentifier": "missing",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        enqueue.assert_not_called()
        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)

    @patch("files.services.document_reprocessing_service.enqueue")
    def test_queue_and_duplicate_request(self, enqueue):
        response = self.client.post(
            self.url, {"action": "reparse", "processingMode": "advanced"}, format="json"
        )
        self.assertEqual(response.status_code, 202, response.data)
        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSING)
        self.assertEqual(self.file.index_generation, "original-index")
        self.assertEqual(self.file.processing_mode, DocumentProcessingMode.BASIC)
        self.assertEqual(enqueue.call_args.kwargs["job_id"], self.file.job_id)
        self.assertEqual(
            self.client.post(
                self.url,
                {"action": "reparse", "processingMode": "basic"},
                format="json",
            ).status_code,
            409,
        )
        self.assertEqual(enqueue.call_count, 1)

    @patch(
        "files.services.document_reprocessing_service.enqueue",
        side_effect=RuntimeError("queue down"),
    )
    def test_queue_failure_restores_file(self, enqueue):
        response = self.client.post(
            self.url, {"action": "reparse", "processingMode": "advanced"}, format="json"
        )
        self.assertEqual(response.status_code, 503)
        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)
        self.assertEqual(self.file.extracted_text, "Original text")
        self.assertIsNone(self.file.job_id)

    def test_validation_and_owner_boundaries(self):
        for data in (
            {"action": "reparse"},
            {"action": "reparse", "processingMode": "legacy"},
            {"action": "retry_images", "processingMode": "basic"},
        ):
            self.assertEqual(
                self.client.post(self.url, data, format="json").status_code, 400
            )
        self.assertEqual(
            self.client.post(
                self.url, {"action": "retry_images"}, format="json"
            ).status_code,
            409,
        )
        other = get_user_model().objects.create_user(
            email="other-reprocess@example.com", password="test"
        )
        self.client.force_authenticate(other)
        self.assertEqual(
            self.client.post(
                self.url,
                {"action": "reparse", "processingMode": "advanced"},
                format="json",
            ).status_code,
            404,
        )
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 401)

    @patch("core.services.document_ingestion_service.DocumentProcessor")
    def test_failed_reparse_restores_document_and_index(self, processor):
        self.file.job_id = "retry-job"
        self.file.status = FileStatus.PROCESSING
        self.file.save()

        def fail(file):
            file.extracted_text = "Replacement text"
            file.parser_name = "docling"
            file.save(update_fields=["extracted_text", "parser_name"])
            raise RuntimeError("parse failed")

        processor.return_value.parse_file.side_effect = fail
        command = DocumentIngestionCommand(
            file_id=self.file.id,
            expected_job_id="retry-job",
            reprocessing_action=DocumentReprocessingAction.REPARSE,
            processing_mode=DocumentProcessingMode.ADVANCED,
            previous_status=FileStatus.PROCESSED,
        )
        with self.assertRaises(RuntimeError):
            DocumentIngestionService().process(command)
        self.file.refresh_from_db()
        self.assertEqual(self.file.extracted_text, "Original text")
        self.assertEqual(self.file.parser_name, "basic")
        self.assertEqual(self.file.processing_mode, "basic")
        self.assertEqual(self.file.status, FileStatus.PROCESSED)
        self.assertEqual(self.file.index_generation, "original-index")
        self.assertIsNone(self.file.ingestion_token)
        self.assertIsNone(DocumentIngestionService().process(command))
        self.assertEqual(processor.return_value.parse_file.call_count, 1)

    def test_replacement_failure_retains_chunks_and_vectors_then_success_switches(self):
        from core.services.document_processor import DocumentProcessor
        from core.test_document_ingestion_map import patched_ingestion
        from files.models import DocumentChunk

        old = DocumentChunk.objects.create(
            file=self.file, chunk_index=0, text="Old chunk"
        )
        self.file.job_id = "replacement-job"
        self.file.status = FileStatus.PROCESSING
        self.file.save()
        command = DocumentIngestionCommand(
            file_id=self.file.pk,
            expected_job_id="replacement-job",
            reprocessing_action=DocumentReprocessingAction.REPARSE,
            processing_mode=DocumentProcessingMode.ADVANCED,
            previous_status=FileStatus.PROCESSED,
        )
        with patched_ingestion(), patch.object(
            DocumentProcessor,
            "_store_vectors",
            side_effect=RuntimeError("vector store down"),
        ), patch.object(DocumentProcessor, "_retire_index") as retire:
            with self.assertRaises(Exception):
                DocumentIngestionService().process(command)
            self.assertTrue(
                DocumentChunk.objects.filter(pk=old.pk, text="Old chunk").exists()
            )
            self.file.refresh_from_db()
            self.assertEqual(self.file.index_generation, "original-index")
            self.assertEqual(self.file.extracted_text, "Original text")
            self.assertNotIn(
                "original-index", [call.args[0] for call in retire.call_args_list]
            )

        self.file.status = FileStatus.PROCESSING
        self.file.save()
        with patched_ingestion(), patch.object(
            DocumentProcessor, "_retire_index"
        ) as retire, self.captureOnCommitCallbacks(execute=True):
            self.assertGreater(DocumentIngestionService().process(command), 0)
        self.file.refresh_from_db()
        self.assertNotEqual(self.file.index_generation, "original-index")
        self.assertEqual(self.file.processing_mode, DocumentProcessingMode.ADVANCED)
        self.assertFalse(DocumentChunk.objects.filter(pk=old.pk).exists())
        retire.assert_called_once()
        self.assertEqual(retire.call_args.args[0], "original-index")

    def test_empty_replacement_retains_old_index(self):
        from core.test_document_ingestion_map import patched_ingestion

        self.file.job_id = "empty-job"
        self.file.status = FileStatus.PROCESSING
        self.file.save()
        with patched_ingestion(embed_side_effect=lambda *args, **kwargs: []), patch(
            "core.services.document_processor.DocumentProcessor._retire_index"
        ):
            with self.assertRaises(Exception):
                DocumentIngestionService().process(
                    DocumentIngestionCommand(
                        file_id=self.file.pk,
                        expected_job_id="empty-job",
                        reprocessing_action=DocumentReprocessingAction.REPARSE,
                        processing_mode=DocumentProcessingMode.ADVANCED,
                        previous_status=FileStatus.PROCESSED,
                    )
                )
        self.file.refresh_from_db()
        self.assertEqual(self.file.index_generation, "original-index")
        self.assertEqual(self.file.extracted_text, "Original text")
