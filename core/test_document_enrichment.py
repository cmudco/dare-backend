from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from PIL import Image, ImageDraw

from core.config.document_parsing import ElementKind, ElementLabel
from core.services.document_crop_service import DocumentCropService
from core.services.document_enrichment_service import (
    DocumentEnrichmentService,
    EnrichmentTelemetry,
)
from core.services.document_parsers.docling_parser import DoclingDocumentParser
from core.services.dtos.parsed_document_dto import (
    BoundingBox,
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)
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

    def test_provider_control_characters_are_removed_before_persistence(self):
        file = SimpleNamespace(save=Mock())
        payload = {"elements": [{"description": "figure\x00text"}]}

        DocumentEnrichmentService._persist(file, "page\x00text", payload)

        self.assertEqual(file.extracted_text, "page text")
        self.assertEqual(
            file.document_model["elements"][0]["description"], "figure text"
        )


class BlankPageDetectionTests(SimpleTestCase):
    def test_only_an_effectively_white_raster_is_blank(self):
        white = Image.new("RGB", (100, 100), "white")
        marked = white.copy()
        ImageDraw.Draw(marked).text((10, 10), "faint scan", fill=(210, 210, 210))

        self.assertTrue(
            DocumentCropService.is_blank_page(DocumentCropService._encode(white))
        )
        self.assertFalse(
            DocumentCropService.is_blank_page(DocumentCropService._encode(marked))
        )

    def test_reuse_replaces_a_stored_hallucination_with_blank_result(self):
        crop = SimpleNamespace(
            render_page=lambda *_args: b"blank",
            is_blank_page=lambda _image: True,
        )
        service = DocumentEnrichmentService(crop_service=crop)
        parsed = ParsedDocument(parser="docling", structure=DocumentStructure(pages=1))
        payload = {
            **parsed.to_dict(),
            "page_enrichments": [
                {
                    "page_no": 1,
                    "status": "complete",
                    "kind": "page_transcription",
                    "transcription_markdown": "Invented content",
                }
            ],
        }

        with patch.object(service, "_persist"):
            result = service._reuse_stored(SimpleNamespace(), parsed, payload)

        self.assertEqual(
            result.document_model["page_enrichments"][0]["kind"], "blank_page"
        )
        self.assertEqual(result.processed_pages, 1)
        self.assertEqual(result.blank_pages, 1)
        self.assertEqual(result.transcribed_pages, 0)
        self.assertEqual(result.text, "")


class DocumentEnrichmentTelemetryTests(SimpleTestCase):
    def setUp(self):
        self.file = SimpleNamespace(
            id=7,
            name="article.pdf",
            file=SimpleNamespace(name="files/article.pdf"),
            user=SimpleNamespace(id=3),
        )
        self.route = SimpleNamespace(
            model=SimpleNamespace(identifier="gemini-test"),
            wallet_type="LITELLM",
            litellm_key=None,
        )
        self.service = DocumentEnrichmentService()

    def test_blank_page_never_reaches_the_vision_provider(self):
        crop = SimpleNamespace(
            render_page=lambda *_args: b"blank",
            is_blank_page=lambda _image: True,
        )
        service = DocumentEnrichmentService(crop_service=crop)
        telemetry = EnrichmentTelemetry()
        ai_service = SimpleNamespace(generate_structured_output_with_usage=AsyncMock())

        result = service._transcribe_page(
            self.file, 11, self.route, ai_service, telemetry
        )

        self.assertEqual(result["kind"], "blank_page")
        self.assertEqual(result["provenance"], "machine_routing")
        self.assertEqual(telemetry.provider_requests, 0)
        ai_service.generate_structured_output_with_usage.assert_not_called()

    @patch("core.services.document_enrichment_service.DocumentEnrichmentCache")
    def test_cache_hit_is_not_counted_as_provider_request(self, cache_model):
        cache_model.objects.filter.return_value.first.return_value = SimpleNamespace(
            result={"description": "cached"}
        )
        telemetry = EnrichmentTelemetry()
        ai_service = SimpleNamespace(generate_structured_output_with_usage=AsyncMock())

        result, cache_hit = self.service._generate_cached(
            file=self.file,
            image=b"image",
            content_sha256="a" * 64,
            context={"page_no": 1},
            prompt="Describe",
            schema={"type": "object"},
            route=self.route,
            ai_service=ai_service,
            output_limit=100,
            kind="figure_description",
            telemetry=telemetry,
        )

        self.assertTrue(cache_hit)
        self.assertEqual(result, {"description": "cached"})
        self.assertEqual(telemetry.cache_hits, 1)
        self.assertEqual(telemetry.provider_requests, 0)
        ai_service.generate_structured_output_with_usage.assert_not_called()

    @patch("core.services.document_enrichment_service.DocumentEnrichmentCache")
    def test_fresh_request_is_counted_before_provider_execution(self, cache_model):
        cache_model.objects.filter.return_value.first.return_value = None
        telemetry = EnrichmentTelemetry()
        ai_service = SimpleNamespace(
            generate_structured_output_with_usage=AsyncMock(
                side_effect=RuntimeError("provider unavailable")
            )
        )

        with (
            patch.object(self.service, "_check_credit"),
            self.assertRaisesRegex(RuntimeError, "provider unavailable"),
        ):
            self.service._generate_cached(
                file=self.file,
                image=b"image",
                content_sha256="a" * 64,
                context={"page_no": 1},
                prompt="Describe",
                schema={"type": "object"},
                route=self.route,
                ai_service=ai_service,
                output_limit=100,
                kind="figure_description",
                telemetry=telemetry,
            )

        self.assertEqual(telemetry.cache_hits, 0)
        self.assertEqual(telemetry.provider_requests, 1)


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

    def test_rebuild_keeps_native_paragraph_that_docling_clipped(self):
        complete = (
            "Kenneth Walker earned the Super Bowl MVP award — making him the first "
            "running back to win Super Bowl MVP since Terrell Davis 28 years ago."
        )
        parsed = ParsedDocument(
            text=complete[:80],
            recovery_text=complete,
            parser="docling",
            structure=DocumentStructure(
                pages=1, pictures=1, content_chars=len(complete)
            ),
            elements=(
                ParsedElement(
                    order=1,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.TEXT,
                    page_no=1,
                    text=complete[:80],
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
            {
                2: {
                    "status": "complete",
                    "description": "A football player carries the ball.",
                    "visible_text": "",
                }
            },
            {},
        )

        self.assertIn("first running back to win Super Bowl MVP", text)
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

        self.assertNotIn("Page 1", text)
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
    def test_persisted_model_rehydrates_without_enrichment_fields(self):
        parsed = ParsedDocument.from_persisted(
            "Original parser text",
            {
                "parser": "docling",
                "duration_seconds": 2.4,
                "counts": {"pages": 2, "pages_without_text": 1},
                "elements": [
                    {
                        "order": 1,
                        "kind": "picture",
                        "label": "picture",
                        "page_no": 2,
                        "bbox": {
                            "left": 0.1,
                            "top": 0.2,
                            "width": 0.3,
                            "height": 0.4,
                        },
                        "enrichment": {"status": "complete"},
                    }
                ],
                "chunk_elements": [
                    {
                        "order": 1,
                        "kind": "picture",
                        "label": "picture",
                        "page_no": 2,
                    }
                ],
                "chunk_elements_lossless": True,
            },
        )

        self.assertEqual(parsed.text, "Original parser text")
        self.assertEqual(parsed.parser, "docling")
        self.assertEqual(parsed.structure.pages, 2)
        self.assertEqual(parsed.elements[0].bbox.width, 0.3)

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
        self.route = SimpleNamespace(model=self.model, litellm_key=None)
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
            patch.object(service, "_resolve_route", return_value=self.route),
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
            patch.object(service, "_resolve_route", return_value=self.route),
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

    @patch(
        "core.services.document_enrichment_service.get_dispatch_credentials_for_user_sync"
    )
    def test_continuation_only_transcribes_unfinished_pages(self, credentials):
        credentials.return_value = self.credentials
        parsed = ParsedDocument(
            parser="docling",
            structure=DocumentStructure(
                pages=3, pictures=0, pages_without_text=3, content_chars=0
            ),
        )
        self.file.document_model = {
            **parsed.to_dict(),
            "page_enrichments": [
                {
                    "page_no": 1,
                    "status": "complete",
                    "kind": "page_transcription",
                    "transcription_markdown": "Already complete",
                }
            ],
        }
        service = DocumentEnrichmentService()

        def transcribe(_file, page_no, *_args):
            return {
                "status": "complete",
                "kind": "page_transcription",
                "transcription_markdown": f"Page {page_no}",
            }

        with (
            patch.object(service, "_resolve_route", return_value=self.route),
            patch.object(service, "_build_ai_service", return_value=object()),
            patch.object(service, "_transcribe_page", side_effect=transcribe) as call,
            patch.object(service, "_describe_figure") as describe,
            patch.object(service, "_persist"),
        ):
            result = service.enrich(
                self.file, parsed, page_limit=1, continue_existing=True
            )

        self.assertEqual([row.args[1] for row in call.call_args_list], [2])
        describe.assert_not_called()
        self.assertEqual(result.transcribed_pages, 2)
        self.assertEqual(
            result.document_model["enrichment"]["deferred_textless_pages"], 1
        )
        self.assertIn("Already complete", result.text)
        self.assertIn("Page 2", result.text)
