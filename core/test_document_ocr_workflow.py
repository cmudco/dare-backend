from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from core.services.document_ocr_workflow_service import DocumentOcrWorkflowService
from core.services.dtos.parsed_document_dto import DocumentStructure, ParsedDocument
from core.services.vision_model_service import _cost_per_page
from core.test_document_ingestion_map import patched_ingestion
from files.constants import DocumentOcrStatus, FileStatus
from files.models import DocumentChunk, DocumentOcrRequest, File


def make_file():
    return SimpleNamespace(
        id=7,
        file=SimpleNamespace(name="files/scanned.pdf"),
        user=SimpleNamespace(id=3, vision_model=""),
    )


def make_parsed(textless_pages: int, pages: int | None = None):
    return SimpleNamespace(
        text="",
        parser="docling",
        is_page_based=True,
        structure=SimpleNamespace(
            pages=pages or textless_pages,
            pages_without_text=textless_pages,
        ),
    )


def make_request(**overrides):
    defaults = {
        "status": DocumentOcrStatus.AWAITING_APPROVAL,
        "detected_pages": 0,
        "page_limit": 10,
        "max_page_limit": 100,
        "estimated_cost_per_page": Decimal("0"),
        "model_identifier": "",
        "chunk_size": None,
        "overlap_size": None,
        "processed_pages": 0,
        "parsed_text": None,
        "save": MagicMock(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@override_settings()
class DocumentOcrWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.service = DocumentOcrWorkflowService()
        self.model = SimpleNamespace(
            model=SimpleNamespace(identifier="gemini-vision"),
            estimated_cost_per_page=Decimal("0.0025"),
        )

    @patch("core.services.document_ocr_workflow_service.resolve_vision_model")
    @patch(
        "core.services.document_ocr_workflow_service.DocumentOcrRequest.objects.get_or_create"
    )
    def test_large_scan_pauses_before_vision_calls(self, get_or_create, resolve):
        request = make_request()
        get_or_create.return_value = (request, True)
        resolve.return_value = self.model

        plan = self.service.prepare(make_file(), make_parsed(42), 1500, 180)

        self.assertTrue(plan.should_pause)
        self.assertIsNone(plan.page_limit)
        self.assertEqual(request.status, DocumentOcrStatus.AWAITING_APPROVAL)
        self.assertEqual(request.page_limit, 10)
        self.assertEqual(get_or_create.call_args.kwargs["defaults"]["parsed_text"], "")
        request.save.assert_called_once()

    @patch("core.services.document_ocr_workflow_service.resolve_vision_model")
    @patch(
        "core.services.document_ocr_workflow_service.DocumentOcrRequest.objects.get_or_create"
    )
    def test_small_scan_runs_automatically(self, get_or_create, resolve):
        request = make_request(page_limit=6)
        get_or_create.return_value = (request, True)
        resolve.return_value = self.model

        plan = self.service.prepare(make_file(), make_parsed(6))

        self.assertFalse(plan.should_pause)
        self.assertEqual(plan.page_limit, 6)
        self.assertEqual(request.status, DocumentOcrStatus.PROCESSING)

    @patch("core.services.document_ocr_workflow_service.resolve_vision_model")
    @patch(
        "core.services.document_ocr_workflow_service.DocumentOcrRequest.objects.get_or_create"
    )
    def test_partial_scan_stays_paused_until_user_continues(
        self, get_or_create, resolve
    ):
        request = make_request(
            status=DocumentOcrStatus.PARTIAL,
            detected_pages=80,
            processed_pages=10,
            page_limit=50,
            parsed_text="",
        )
        get_or_create.return_value = (request, False)
        resolve.return_value = self.model

        plan = self.service.prepare(make_file(), make_parsed(80))

        self.assertTrue(plan.should_pause)
        self.assertEqual(request.page_limit, 10)
        self.assertEqual(request.status, DocumentOcrStatus.PARTIAL)

    @patch("core.services.document_ocr_workflow_service.resolve_vision_model")
    @patch(
        "core.services.document_ocr_workflow_service.DocumentOcrRequest.objects.get_or_create"
    )
    def test_approved_scan_resumes_with_selected_limit(self, get_or_create, resolve):
        request = make_request(status=DocumentOcrStatus.APPROVED, page_limit=25)
        get_or_create.return_value = (request, False)
        resolve.return_value = self.model

        plan = self.service.prepare(make_file(), make_parsed(80))

        self.assertFalse(plan.should_pause)
        self.assertEqual(plan.page_limit, 25)
        self.assertEqual(request.status, DocumentOcrStatus.PROCESSING)

    @patch("core.services.vision_model_service.BillingService")
    def test_cost_estimate_uses_configured_per_page_token_budget(self, billing):
        billing.return_value._calculate_estimated_cost.return_value = Decimal("0.0042")
        rates = SimpleNamespace(identifier="gemini-vision")

        result = _cost_per_page(rates)

        self.assertEqual(result, Decimal("0.0042"))
        billing.return_value._calculate_estimated_cost.assert_called_once_with(
            rates, input_tokens=5000, output_tokens=2000
        )


class DocumentIngestionResumeTests(SimpleTestCase):
    def test_approved_continuation_reuses_persisted_parse(self):
        file = SimpleNamespace(
            document_model={
                "parser": "docling",
                "counts": {"pages": 12, "pages_without_text": 12},
                "elements": [],
                "chunk_elements_lossless": True,
            }
        )
        request = SimpleNamespace(
            status=DocumentOcrStatus.APPROVED,
            parsed_text="",
            processed_pages=10,
        )
        processor = MagicMock()
        processor.parsing_service.attach_pdf_recovery_text.side_effect = (
            lambda file, parsed: parsed
        )
        stage = MagicMock()
        journey = MagicMock()
        journey.stage.return_value.__enter__.return_value = stage

        parsed, continuing = DocumentIngestionService._load_or_parse(
            file, request, processor, journey
        )

        self.assertTrue(continuing)
        self.assertEqual(parsed.parser, "docling")
        self.assertEqual(parsed.structure.pages, 12)
        processor.parse_file.assert_not_called()
        stage.skip.assert_called_once()

    def test_legacy_truncated_chunk_elements_are_reparsed_once(self):
        file = SimpleNamespace(
            document_model={
                "parser": "docling",
                "counts": {"pages": 2, "pages_without_text": 1},
                "elements_truncated": True,
                "chunk_elements": [{"order": 1, "text": "clipped"}],
            }
        )
        request = SimpleNamespace(
            status=DocumentOcrStatus.COMPLETE,
            parsed_text="old text",
            processed_pages=1,
            save=MagicMock(),
        )
        repaired = ParsedDocument(
            text="complete native text",
            structure=DocumentStructure(pages=2, pages_without_text=1),
            parser="docling",
        )
        processor = MagicMock()
        processor.parse_file.return_value = repaired
        journey = MagicMock()
        journey.stage.return_value.__enter__.return_value = MagicMock()

        with patch.object(DocumentIngestionService, "_restore_enrichment_results"):
            parsed, continuing = DocumentIngestionService._load_or_parse(
                file, request, processor, journey
            )

        self.assertEqual(parsed, repaired)
        self.assertFalse(continuing)
        processor.parse_file.assert_called_once_with(file)
        self.assertEqual(request.parsed_text, "complete native text")
        request.save.assert_called_once_with(
            update_fields=["parsed_text", "updated_at"]
        )

    def test_lossless_truncated_chunk_elements_can_be_reused(self):
        file = SimpleNamespace(
            document_model={
                "parser": "docling",
                "counts": {"pages": 2, "pages_without_text": 1},
                "elements_truncated": True,
                "chunk_elements_lossless": True,
                "chunk_elements": [{"order": 1, "kind": "text", "label": "text"}],
            }
        )
        request = SimpleNamespace(
            status=DocumentOcrStatus.COMPLETE,
            parsed_text="complete native text",
            processed_pages=1,
        )
        processor = MagicMock()
        processor.parsing_service.attach_pdf_recovery_text.side_effect = (
            lambda file, parsed: parsed
        )
        journey = MagicMock()
        journey.stage.return_value.__enter__.return_value = MagicMock()

        parsed, continuing = DocumentIngestionService._load_or_parse(
            file, request, processor, journey
        )

        self.assertTrue(continuing)
        self.assertEqual(parsed.text, "complete native text")
        processor.parse_file.assert_not_called()


ENRICHMENT = "core.services.document_enrichment_service.DocumentEnrichmentService"
PROCESSOR = "core.services.document_processor.DocumentProcessor"

TRANSCRIBED_TEXT = "Pension certificate 4471 issued to Elias Boudinot on 3 May 1867"
PRINTED_PARAGRAPH = (
    "The clerk recorded the claim in the docket book and forwarded it to the "
    "pension agency for review, noting the certificate number in the margin so "
    "that the examiner could match it against the surviving muster rolls."
)


def page_transcription(page_no):
    """One stored page transcription, shaped as the enrichment service writes it."""
    return {
        "page_no": page_no,
        "status": "complete",
        "kind": "page_transcription",
        "transcription_markdown": f"{TRANSCRIBED_TEXT} (page {page_no}).",
        "summary": f"A handwritten pension record, page {page_no}.",
        "uncertainty": "",
        "model": "gemini-vision",
        "prompt_version": "docling-context-v1",
        "provenance": "machine_generated",
    }


def scanned_document_model(pages, transcribed, elements=True):
    """A stored parse whose first ``pages - 1`` pages are scans, some transcribed.

    The last page carries printed text, so the fixture exercises both lanes the
    chunker has to reassemble: parsed elements and page transcriptions.
    """
    return {
        "parser": "docling",
        "chunk_elements_lossless": True,
        "counts": {
            "pages": pages,
            "pages_without_text": pages - 1,
            "sections": 0,
            "tables": 0,
            "pictures": 0,
            "content_chars": len(PRINTED_PARAGRAPH),
        },
        "elements": (
            [
                {
                    "order": 1,
                    "kind": "text",
                    "label": "text",
                    "page_no": pages,
                    "text": PRINTED_PARAGRAPH,
                }
            ]
            if elements
            else []
        ),
        "page_enrichments": [page_transcription(n) for n in range(1, transcribed + 1)],
        "enrichment": {
            "status": "complete" if transcribed >= pages - 1 else "partial",
            "model": "gemini-vision",
            "transcribed_pages": transcribed,
            "detected_textless_pages": pages - 1,
            "provenance": "machine_generated",
        },
    }


@contextmanager
def watched_reingest():
    """Re-ingest with enrichment live, Docling forbidden and vision watched.

    ``patched_ingestion`` enters extra patchers last, so these replace its own
    parse, enrichment-disabled and vector-store stubs. Enrichment has to be
    live for the run to rebuild text from the stored transcriptions; the vision
    doors are mocked so that reaching for a model is an assertable event rather
    than a silent charge.
    """
    stored = []
    transcribe = MagicMock(side_effect=AssertionError("transcribed a page again"))
    resolve_route = MagicMock(side_effect=AssertionError("reached for a vision model"))
    with patched_ingestion(
        extra_patchers=[
            patch(f"{ENRICHMENT}._enabled", return_value=True),
            patch(f"{ENRICHMENT}._resolve_route", resolve_route),
            patch(f"{ENRICHMENT}._transcribe_page", transcribe),
            patch(
                f"{PROCESSOR}.parse_file",
                side_effect=AssertionError("re-parsed the PDF with Docling"),
            ),
            patch(
                f"{PROCESSOR}._store_vectors",
                side_effect=lambda vectors, user_id, file_id: stored.append(vectors),
            ),
        ]
    ):
        yield SimpleNamespace(
            stored=stored, transcribe=transcribe, resolve_route=resolve_route
        )


class DocumentIngestionOcrReuseTests(TestCase):
    """Re-ingesting a transcribed scan must rebuild it, not empty it.

    ``refresh_file_embeddings`` deletes the file's vectors and map rows before
    re-running ingestion, so a skipped re-ingest is silent data loss.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="ocr-reuse@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="pension.pdf",
            file=SimpleUploadedFile("pension.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )

    def _store(self, status, pages, transcribed, parsed_text=None, elements=True):
        self.file.document_model = scanned_document_model(pages, transcribed, elements)
        self.file.page_count = pages
        self.file.pages_without_text = pages - 1
        self.file.status = FileStatus.PROCESSED
        self.file.save(
            update_fields=[
                "document_model",
                "page_count",
                "pages_without_text",
                "status",
            ]
        )
        return DocumentOcrRequest.objects.create(
            file=self.file,
            status=status,
            detected_pages=pages - 1,
            processed_pages=transcribed,
            page_limit=7,
            max_page_limit=100,
            parsed_text=PRINTED_PARAGRAPH if parsed_text is None else parsed_text,
        )

    def _process(self):
        return DocumentIngestionService().process(
            DocumentIngestionCommand.from_raw(
                self.file.id, chunk_size=300, overlap_size=40
            )
        )

    def _embedded_texts(self, run):
        self.assertTrue(run.stored, "no vectors reached the vector service")
        return [metadata["text"] for _, _, metadata in run.stored[0]]

    def test_complete_ocr_rebuilds_from_stored_transcriptions(self):
        request = self._store(DocumentOcrStatus.COMPLETE, pages=3, transcribed=2)

        with watched_reingest() as run:
            count = self._process()

        self.assertTrue(count)
        self.assertTrue(
            any(TRANSCRIBED_TEXT in text for text in self._embedded_texts(run))
        )
        self.assertEqual(DocumentChunk.objects.filter(file=self.file).count(), count)
        run.transcribe.assert_not_called()
        run.resolve_route.assert_not_called()

        request.refresh_from_db()
        self.assertEqual(request.status, DocumentOcrStatus.COMPLETE)
        self.assertEqual(request.processed_pages, 2)
        self.assertEqual(request.page_limit, 7)

        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)

    def test_partial_ocr_rebuilds_without_transcribing_the_remainder(self):
        request = self._store(DocumentOcrStatus.PARTIAL, pages=78, transcribed=40)

        with watched_reingest() as run:
            count = self._process()

        self.assertTrue(count)
        self.assertTrue(
            any(TRANSCRIBED_TEXT in text for text in self._embedded_texts(run))
        )
        self.assertEqual(DocumentChunk.objects.filter(file=self.file).count(), count)
        run.transcribe.assert_not_called()
        run.resolve_route.assert_not_called()

        request.refresh_from_db()
        self.assertEqual(request.status, DocumentOcrStatus.PARTIAL)
        self.assertEqual(request.processed_pages, 40)
        self.assertEqual(request.page_limit, 7)

    def test_empty_parsed_text_still_reuses_the_persisted_parse(self):
        """Two of the observed files stored ``""`` — an empty parse, not a missing one."""
        self._store(
            DocumentOcrStatus.COMPLETE,
            pages=2,
            transcribed=1,
            parsed_text="",
            elements=False,
        )

        with watched_reingest() as run:
            count = self._process()

        self.assertTrue(count)
        self.assertTrue(
            any(TRANSCRIBED_TEXT in text for text in self._embedded_texts(run))
        )
        self.assertTrue(DocumentChunk.objects.filter(file=self.file).exists())
        run.transcribe.assert_not_called()

    def test_awaiting_approval_still_skips_and_writes_nothing(self):
        request = self._store(
            DocumentOcrStatus.AWAITING_APPROVAL, pages=3, transcribed=0
        )

        with watched_reingest() as run:
            count = self._process()

        self.assertIsNone(count)
        self.assertEqual(run.stored, [])
        self.assertFalse(DocumentChunk.objects.filter(file=self.file).exists())
        run.transcribe.assert_not_called()

        request.refresh_from_db()
        self.assertEqual(request.status, DocumentOcrStatus.AWAITING_APPROVAL)
        self.assertEqual(request.page_limit, 7)
