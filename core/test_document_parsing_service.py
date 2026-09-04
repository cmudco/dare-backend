from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from PyPDF2.errors import PdfReadError

from core.services.document_parsers.legacy_parser import LegacyDocumentParser
from core.services.document_parsing_service import DocumentParsingService
from core.services.dtos.parsed_document_dto import (
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)


class DocumentParsingServiceTests(SimpleTestCase):
    @patch(
        "core.services.document_parsing_service.get_docling_unavailable_reason",
        return_value="missing layout dependency",
    )
    @patch("core.services.document_parsing_service.get_document_parsers")
    @patch("core.services.document_parsers.legacy_parser.read_bytes_as_text")
    def test_docling_startup_failure_is_persisted_when_legacy_parser_runs(
        self, read_legacy, get_parsers, _unavailable_reason
    ):
        read_legacy.return_value = "Flat recovery text"
        get_parsers.return_value = [LegacyDocumentParser()]
        service = DocumentParsingService()
        file = SimpleNamespace(id=62, save=Mock())

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="fallback.pdf"),
        ):
            parsed = service.parse(file)
            service.persist(file, parsed)

        self.assertEqual(parsed.parser, "legacy")
        self.assertEqual(parsed.fallback_from, "docling")
        self.assertEqual(parsed.fallback_reason, "missing layout dependency")
        self.assertEqual(
            file.document_model["parser_fallback"],
            {"from": "docling", "reason": "missing layout dependency"},
        )

    @patch("core.services.document_parsing_service.sentry_sdk.capture_exception")
    @patch("core.services.document_parsers.legacy_parser.read_bytes_as_text")
    def test_docling_failure_is_reported_and_persisted_with_legacy_fallback(
        self, read_legacy, capture_exception
    ):
        read_legacy.return_value = "Flat recovery text"
        docling = Mock(name="docling_parser")
        docling.name = "docling"
        failure = RuntimeError("layout model unavailable")
        docling.parse.side_effect = failure
        service = DocumentParsingService(parsers=[docling, LegacyDocumentParser()])
        file = SimpleNamespace(id=61, save=Mock())

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="fallback.pdf"),
        ):
            parsed = service.parse(file)
            service.persist(file, parsed)

        self.assertEqual(parsed.parser, "legacy")
        self.assertEqual(parsed.fallback_from, "docling")
        self.assertEqual(parsed.fallback_reason, "layout model unavailable")
        self.assertEqual(
            file.document_model["parser_fallback"],
            {"from": "docling", "reason": "layout model unavailable"},
        )
        capture_exception.assert_called_once_with(failure)

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_pdf_keeps_docling_text_and_uses_independent_native_recovery(
        self, read_native
    ):
        clipped = "Kenneth Walker earned the award, making h"
        complete = (
            "Kenneth Walker earned the award, making him the first running back "
            "to win Super Bowl MVP since Terrell Davis."
        )
        read_native.return_value = complete
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text=clipped,
            elements=(ParsedElement(order=1, kind="text", label="text", text=clipped),),
            structure=DocumentStructure(pages=1, content_chars=len(clipped)),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])
        file = SimpleNamespace(id=56)

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="example.pdf"),
        ):
            parsed = service.parse(file)

        self.assertEqual(parsed.text, clipped)
        self.assertEqual(parsed.recovery_text, complete)
        self.assertEqual(parsed.elements[0].text, clipped)
        self.assertEqual(parsed.parser, "docling")
        read_native.assert_called_once_with(b"pdf", "example.pdf")

        persisted = SimpleNamespace(id=56, save=Mock())
        service.persist(persisted, parsed)
        self.assertIn(complete, persisted.extracted_text)

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_persisted_full_file_text_keeps_docling_table_markdown(self, read_native):
        docling_markdown = "| Criterion | Weight |\n|---|---|\n| Accuracy | 40% |"
        read_native.return_value = "Criterion\nWeight\nAccuracy\n40%"
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text=docling_markdown,
            elements=(),
            structure=DocumentStructure(
                pages=1,
                tables=1,
                content_chars=len(docling_markdown),
            ),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])
        file = SimpleNamespace(id=57, save=Mock())

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="rubric.pdf"),
        ):
            parsed = service.parse(file)
            service.persist(file, parsed)

        self.assertEqual(file.extracted_text, docling_markdown)
        self.assertEqual(parsed.recovery_text, "Criterion\nWeight\nAccuracy\n40%")

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_empty_native_pdf_text_does_not_replace_docling_text(self, read_native):
        read_native.return_value = ""
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text="Docling text",
            structure=DocumentStructure(pages=1, content_chars=12),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="scan.pdf"),
        ):
            parsed = service.parse(SimpleNamespace(id=7))

        self.assertEqual(parsed.text, "Docling text")
        self.assertEqual(parsed.recovery_text, "")

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_native_pdf_failure_cannot_discard_valid_docling_parse(self, read_native):
        read_native.side_effect = PdfReadError("broken native text layer")
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text="Docling still recovered the document.",
            structure=DocumentStructure(pages=1, content_chars=37),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="damaged.pdf"),
        ):
            parsed = service.parse(SimpleNamespace(id=58))

        self.assertEqual(parsed.text, "Docling still recovered the document.")
        self.assertEqual(parsed.recovery_text, "")

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_native_text_is_persisted_when_docling_recovers_no_body(self, read_native):
        read_native.return_value = (
            "A complete native paragraph the parser did not expose."
        )
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text="<!-- image -->",
            structure=DocumentStructure(
                pages=1,
                pages_without_text=1,
                content_chars=0,
            ),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])
        file = SimpleNamespace(id=59, save=Mock())

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="native.pdf"),
        ):
            parsed = service.parse(file)
            service.persist(file, parsed)

        self.assertEqual(
            file.extracted_text,
            "A complete native paragraph the parser did not expose.",
        )

    @patch("core.services.document_parsing_service.read_bytes_as_text")
    def test_invalid_pdf_control_characters_are_removed_before_persistence(
        self, read_native
    ):
        read_native.return_value = (
            "Recovered equation \x00 value \x01 remains readable."
        )
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.parse.return_value = ParsedDocument(
            text="Docling body \x00 remains readable.",
            elements=(
                ParsedElement(
                    order=1,
                    kind="text",
                    label="text",
                    text="Element \x00 body",
                    section="Section \x01 one",
                    heading_context=({"text": "Heading \x00 one"},),
                ),
            ),
            structure=DocumentStructure(pages=1, content_chars=31),
            parser="docling",
        )
        service = DocumentParsingService(parsers=[parser])
        file = SimpleNamespace(id=60, save=Mock())

        with (
            patch.object(service, "_read_bytes", return_value=b"pdf"),
            patch.object(service, "_filename", return_value="controls.pdf"),
        ):
            parsed = service.parse(file)
            service.persist(file, parsed)

        self.assertNotIn("\x00", parsed.text)
        self.assertNotIn("\x00", parsed.recovery_text)
        self.assertEqual(parsed.elements[0].text, "Element   body")
        self.assertEqual(parsed.elements[0].section, "Section   one")
        self.assertEqual(parsed.elements[0].heading_context[0]["text"], "Heading   one")
        self.assertNotIn("\x00", file.extracted_text)
        self.assertNotIn("\x00", str(file.document_model))
