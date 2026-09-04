from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services.document_processor import DocumentProcessor
from core.services.file_processing_journey import FileProcessingJourney


def make_file():
    user = SimpleNamespace(id=7, chunk_size=1000, overlap_size=100)
    return SimpleNamespace(
        pk=42,
        id=42,
        name="journey.pdf",
        file=SimpleNamespace(name="journey.pdf"),
        file_type="application/pdf",
        user=user,
        processing_journey={},
        processing_stage="parsing",
    )


class FileProcessingJourneyTests(SimpleTestCase):
    @patch("core.services.file_processing_journey.File.active_objects.filter")
    def test_records_stage_metrics_and_preserves_retries(self, mocked_filter):
        file = make_file()
        journey = FileProcessingJourney(file)

        journey.begin_attempt()
        with journey.stage("parsing") as stage:
            stage.add_details(pages=3, pictures=2)
        journey.fail_attempt("Vector database unavailable")

        journey.begin_attempt()
        with journey.stage("enriching") as stage:
            stage.skip("No visual content required enrichment.")
        journey.complete_attempt()

        attempts = file.processing_journey["attempts"]
        self.assertEqual([attempt["number"] for attempt in attempts], [1, 2])
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(attempts[0]["stages"][0]["details"]["pages"], 3)
        self.assertEqual(attempts[1]["status"], "complete")
        self.assertEqual(attempts[1]["stages"][0]["status"], "skipped")
        self.assertTrue(mocked_filter.return_value.update.called)

    @patch("core.services.file_processing_journey.File.active_objects.filter")
    def test_exception_is_attached_to_the_active_stage(self, _mocked_filter):
        file = make_file()
        journey = FileProcessingJourney(file)
        journey.begin_attempt()

        with self.assertRaisesMessage(RuntimeError, "Docling stopped"):
            with journey.stage("parsing"):
                raise RuntimeError("Docling stopped")

        journey.fail_attempt("Docling stopped")
        attempt = file.processing_journey["attempts"][0]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["stages"][0]["status"], "failed")
        self.assertEqual(attempt["stages"][0]["error"], "Docling stopped")

    @patch("core.services.file_processing_journey.File.active_objects.filter")
    def test_invalid_controls_cannot_break_failure_persistence(self, _mocked_filter):
        file = make_file()
        journey = FileProcessingJourney(file)
        journey.begin_attempt()

        with self.assertRaises(RuntimeError):
            with journey.stage("parsing") as stage:
                stage.add_details(parser_message="bad\x00detail")
                raise RuntimeError("bad\x00document")

        journey.fail_attempt("bad\x00document")
        attempt = file.processing_journey["attempts"][0]
        self.assertEqual(attempt["error"], "bad document")
        self.assertEqual(attempt["stages"][0]["error"], "bad document")
        self.assertEqual(
            attempt["stages"][0]["details"]["parser_message"], "bad detail"
        )


class DocumentProcessorJourneyTests(SimpleTestCase):
    @patch("core.services.file_processing_journey.File.active_objects.filter")
    @patch.object(
        DocumentProcessor,
        "_embed_with_structure",
        return_value=(
            [("Useful document text", [0.1, 0.2], {"file_id": 42})],
            {"structured": False, "references_found": 0, "references_resolved": 0},
        ),
    )
    def test_vector_failure_is_attributed_to_indexing(
        self, _mocked_embed_with_structure, _mocked_filter
    ):
        file = make_file()
        parsed = SimpleNamespace(
            parser="docling",
            fallback_from=None,
            fallback_reason=None,
            duration_seconds=1.25,
            elements=[
                SimpleNamespace(
                    kind="picture", classifications=[{"label": "photograph"}]
                )
            ],
            structure=SimpleNamespace(
                pages=2,
                sections=1,
                tables=0,
                pictures=1,
            ),
        )
        parsing_service = MagicMock()
        parsing_service.parse_and_persist.return_value = parsed
        enrichment_service = MagicMock()
        enrichment_service.enrich.return_value = SimpleNamespace(
            text="Useful document text",
            document_model={"enrichment": {"status": "not_needed"}},
            attempted_calls=0,
            provider_requests=0,
            cache_hits=0,
            described_figures=0,
            transcribed_pages=0,
            processed_pages=0,
            blank_pages=0,
            failed_calls=0,
        )
        embedding_service = MagicMock()
        vector_service = MagicMock()
        vector_service.upsert_vectors.side_effect = RuntimeError("Weaviate unavailable")
        processor = DocumentProcessor(
            openai_client=MagicMock(),
            vector_service=vector_service,
            embedding_service=embedding_service,
            parsing_service=parsing_service,
            enrichment_service=enrichment_service,
            user_id=file.user.id,
        )

        with self.assertRaisesRegex(Exception, "Weaviate unavailable"):
            processor.create_file_embeddings(file)

        attempt = file.processing_journey["attempts"][0]
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(
            [stage["status"] for stage in attempt["stages"]],
            ["complete", "skipped", "complete", "failed"],
        )
        self.assertEqual(attempt["stages"][-1]["key"], "indexing")
        self.assertIn("Weaviate unavailable", attempt["stages"][-1]["error"])
