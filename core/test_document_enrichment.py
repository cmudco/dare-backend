from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from core.config.document_parsing import ElementKind, ElementLabel
from core.services.document_enrichment_service import DocumentEnrichmentService
from core.services.document_parsers.docling_parser import DoclingDocumentParser
from core.services.dtos.parsed_document_dto import (BoundingBox,
                                                    DocumentStructure,
                                                    ParsedDocument,
                                                    ParsedElement)
from core.services.gemini_service import GeminiService
from files.api.serializers import FileStructureSerializer


class DocumentEnrichmentRoutingTests(SimpleTestCase):
    def picture(self, **overrides):
        defaults = {
            "order": 2,
            "kind": ElementKind.PICTURE,
            "label": "picture",
            "page_no": 1,
            "bbox": BoundingBox(left=0.1, top=0.1, width=0.5, height=0.4),
            "classifications": ({"label": "photograph", "confidence": 0.99},),
        }
        defaults.update(overrides)
        return ParsedElement(**defaults)

    def test_textless_page_routes_to_full_page_transcription(self):
        decision = DocumentEnrichmentService._picture_decision(self.picture(), {1})
        self.assertEqual(decision, "full_page_transcription")

    def test_small_picture_is_skipped_before_paid_call(self):
        picture = self.picture(
            bbox=BoundingBox(left=0.1, top=0.1, width=0.1, height=0.1)
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(picture, set()),
            "small_picture",
        )

    def test_logo_is_skipped_after_local_classification(self):
        picture = self.picture(
            classifications=({"label": "logo", "confidence": 0.997},)
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(picture, set()),
            "class:logo",
        )

    def test_substantive_figure_is_described(self):
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(self.picture(), set()),
            "describe",
        )


class DocumentEnrichmentReadingOrderTests(SimpleTestCase):
    def test_rebuild_places_description_at_picture_position(self):
        parsed = ParsedDocument(
            parser="docling",
            structure=DocumentStructure(pages=1, pictures=1, content_chars=80),
            elements=(
                ParsedElement(
                    order=1,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.SECTION_HEADER,
                    page_no=1,
                    text="Results",
                ),
                ParsedElement(
                    order=2,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                ),
                ParsedElement(
                    order=3,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.TEXT,
                    page_no=1,
                    text="The discussion follows.",
                ),
            ),
        )
        text = DocumentEnrichmentService._rebuild_text(
            parsed,
            {
                2: {
                    "status": "complete",
                    "description": "A line chart rises from left to right.",
                    "visible_text": "2019–2025",
                }
            },
            {},
        )

        self.assertLess(text.index("Results"), text.index("line chart"))
        self.assertLess(text.index("line chart"), text.index("discussion"))
        self.assertIn("Machine-generated figure description", text)

    def test_full_page_transcription_replaces_scan_regions_once(self):
        parsed = ParsedDocument(
            parser="docling",
            structure=DocumentStructure(pages=1, pictures=2),
            elements=(
                ParsedElement(
                    order=1,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                ),
                ParsedElement(
                    order=2,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                ),
            ),
        )
        text = DocumentEnrichmentService._rebuild_text(
            parsed,
            {},
            {
                1: {
                    "status": "complete",
                    "transcription_markdown": "# Archive letter\nDated 1912.",
                }
            },
        )

        self.assertEqual(text.count("machine transcription"), 1)
        self.assertIn("Archive letter", text)

    def test_visual_summary_is_searchable_when_scan_contains_no_text(self):
        parsed = ParsedDocument(
            parser="docling",
            structure=DocumentStructure(pages=1, pictures=0),
        )
        text = DocumentEnrichmentService._rebuild_text(
            parsed,
            {},
            {
                1: {
                    "status": "complete",
                    "transcription_markdown": "",
                    "summary": "Seven people play hockey on a frozen pond at night.",
                }
            },
        )

        self.assertIn("hockey", text)
        self.assertIn("Machine-generated page description", text)


class DocumentModelContextTests(SimpleTestCase):
    def test_context_and_classifier_provenance_are_serialized(self):
        element = ParsedElement(
            order=4,
            kind=ElementKind.PICTURE,
            label="picture",
            page_no=2,
            tree_depth=1,
            heading_context=(
                {"order": 1, "page_no": 1, "text": "News"},
                {"order": 3, "page_no": 2, "text": "Brain mapping"},
            ),
            classifications=({"label": "photograph", "confidence": 0.98},),
            content_sha256="a" * 64,
        )

        payload = element.to_dict()
        self.assertEqual(payload["heading_context"][-1]["text"], "Brain mapping")
        self.assertEqual(payload["classifications"][0]["label"], "photograph")
        self.assertEqual(payload["content_sha256"], "a" * 64)

    def test_gemini_schema_removes_only_unsupported_strict_keyword(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "additionalProperties": False,
                }
            },
            "required": ["nested"],
            "additionalProperties": False,
        }

        sanitized = GeminiService._sanitize_response_schema(schema)
        self.assertNotIn("additionalProperties", sanitized)
        self.assertNotIn("additionalProperties", sanitized["properties"]["nested"])
        self.assertEqual(sanitized["required"], ["nested"])

    def test_parser_retries_without_optional_classifier(self):
        document = SimpleNamespace(
            iterate_items=lambda: iter(()),
            pages={},
            pictures=[],
            tables=[],
            export_to_markdown=lambda: "",
        )
        failing = SimpleNamespace(
            convert=lambda _source: (_ for _ in ()).throw(
                RuntimeError("classifier unavailable")
            )
        )
        fallback = SimpleNamespace(
            convert=lambda _source: SimpleNamespace(document=document)
        )
        parser = DoclingDocumentParser()

        with (
            patch.object(parser, "_get_converter", return_value=failing),
            patch.object(
                parser,
                "_get_classification_fallback_converter",
                return_value=fallback,
            ),
        ):
            parsed = parser.parse(b"not-used-by-fakes", "sample.pdf")

        self.assertEqual(parsed.parser, "docling")

    def test_structure_overview_omits_full_page_transcription(self):
        file = SimpleNamespace(
            document_model={
                "page_enrichments": [
                    {
                        "page_no": 1,
                        "status": "complete",
                        "summary": "A letter",
                        "transcription_markdown": "Full text " * 100,
                    }
                ]
            }
        )
        overview = FileStructureSerializer(context={"page_no": None})
        page = FileStructureSerializer(context={"page_no": 1})

        self.assertNotIn(
            "transcription_markdown", overview.get_page_enrichments(file)[0]
        )
        self.assertIn("transcription_markdown", page.get_page_enrichments(file)[0])


class DocumentEnrichmentOrchestrationTests(SimpleTestCase):
    def setUp(self):
        self.file = SimpleNamespace(
            id=7,
            name="article.pdf",
            file=SimpleNamespace(name="files/article.pdf"),
            user=SimpleNamespace(id=3),
        )
        self.model = SimpleNamespace(identifier="gemini-test", provider="gemini")
        self.credentials = SimpleNamespace(use_litellm_proxy=False)

    @patch(
        "core.services.document_enrichment_service.get_dispatch_credentials_for_user_sync"
    )
    def test_mixed_document_routes_only_substantive_figure(self, credentials):
        credentials.return_value = self.credentials
        parsed = ParsedDocument(
            parser="docling",
            text="Source text",
            structure=DocumentStructure(
                pages=1, pictures=2, pages_without_text=0, content_chars=100
            ),
            elements=(
                ParsedElement(
                    order=1,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.SECTION_HEADER,
                    page_no=1,
                    text="Findings",
                ),
                ParsedElement(
                    order=2,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.TEXT,
                    page_no=1,
                    text="The procedure was documented in the surrounding article text.",
                ),
                ParsedElement(
                    order=3,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                    bbox=BoundingBox(0.1, 0.1, 0.5, 0.4),
                    classifications=({"label": "photograph", "confidence": 0.99},),
                ),
                ParsedElement(
                    order=4,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                    bbox=BoundingBox(0.1, 0.1, 0.1, 0.1),
                    classifications=({"label": "logo", "confidence": 0.99},),
                ),
            ),
        )
        service = DocumentEnrichmentService()

        with (
            patch.object(service, "_resolve_model", return_value=self.model),
            patch.object(service, "_build_ai_service", return_value=object()),
            patch.object(
                service,
                "_describe_figure",
                return_value={
                    "status": "complete",
                    "kind": "figure_description",
                    "description": "A surgical team works around a patient.",
                    "visible_text": "",
                },
            ) as describe,
            patch.object(service, "_persist"),
        ):
            result = service.enrich(self.file, parsed)

        self.assertEqual(describe.call_count, 1)
        self.assertEqual(result.described_figures, 1)
        self.assertIn("surgical team", result.text)
        small = next(
            row for row in result.document_model["elements"] if row["order"] == 4
        )
        self.assertEqual(small["enrichment"]["reason"], "small_picture")

    @patch(
        "core.services.document_enrichment_service.get_dispatch_credentials_for_user_sync"
    )
    def test_scan_routes_once_per_page_not_once_per_picture(self, credentials):
        credentials.return_value = self.credentials
        parsed = ParsedDocument(
            parser="docling",
            structure=DocumentStructure(
                pages=1, pictures=2, pages_without_text=1, content_chars=0
            ),
            elements=(
                ParsedElement(
                    order=1,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                ),
                ParsedElement(
                    order=2,
                    kind=ElementKind.PICTURE,
                    label="picture",
                    page_no=1,
                ),
            ),
        )
        service = DocumentEnrichmentService()

        with (
            patch.object(service, "_resolve_model", return_value=self.model),
            patch.object(service, "_build_ai_service", return_value=object()),
            patch.object(
                service,
                "_transcribe_page",
                return_value={
                    "status": "complete",
                    "kind": "page_transcription",
                    "transcription_markdown": "# Letter\nWritten in 1912.",
                },
            ) as transcribe,
            patch.object(service, "_describe_figure") as describe,
            patch.object(service, "_persist"),
        ):
            result = service.enrich(self.file, parsed)

        self.assertEqual(transcribe.call_count, 1)
        describe.assert_not_called()
        self.assertEqual(result.transcribed_pages, 1)
        self.assertIn("Written in 1912", result.text)
