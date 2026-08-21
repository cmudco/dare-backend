from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from core.services.document_ingestion_service import DocumentIngestionService
from core.services.document_ocr_workflow_service import DocumentOcrWorkflowService
from files.constants import DocumentOcrStatus


def make_file():
    return SimpleNamespace(
        id=7,
        file=SimpleNamespace(name="files/scanned.pdf"),
        user=SimpleNamespace(id=3),
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
            identifier="gemini-vision",
            input_token_rate_per_million=Decimal("0.10"),
            output_token_rate_per_million=Decimal("0.40"),
        )

    @patch(
        "core.services.document_ocr_workflow_service.DocumentEnrichmentService._resolve_model"
    )
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

    @patch(
        "core.services.document_ocr_workflow_service.DocumentEnrichmentService._resolve_model"
    )
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

    @patch(
        "core.services.document_ocr_workflow_service.DocumentEnrichmentService._resolve_model"
    )
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

    @patch(
        "core.services.document_ocr_workflow_service.DocumentEnrichmentService._resolve_model"
    )
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

    @patch(
        "core.services.document_ocr_workflow_service.BillingService._calculate_estimated_cost"
    )
    def test_cost_estimate_uses_configured_per_page_token_budget(self, calculate):
        calculate.return_value = Decimal("0.0042")

        result = self.service._cost_per_page(self.model)

        self.assertEqual(result, Decimal("0.0042"))
        calculate.assert_called_once_with(
            self.model, input_tokens=5000, output_tokens=2000
        )


class DocumentIngestionResumeTests(SimpleTestCase):
    def test_approved_continuation_reuses_persisted_parse(self):
        file = SimpleNamespace(
            document_model={
                "parser": "docling",
                "counts": {"pages": 12, "pages_without_text": 12},
                "elements": [],
            }
        )
        request = SimpleNamespace(
            status=DocumentOcrStatus.APPROVED,
            parsed_text="",
            processed_pages=10,
        )
        processor = MagicMock()
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
