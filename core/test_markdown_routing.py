from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from core.services.document_parsing_service import DocumentParsingService

MARKDOWN = b"# Guide\n\n## Prices\n\n| Item | Price |\n| --- | --- |\n| Apple | 3 |\n\n- First item\n- Second item\n"
CSV = b"item,price\napple,3\npear,4\n"


class MarkdownRoutingTests(SimpleTestCase):
    def file(self, mode, name, data=MARKDOWN):
        return SimpleNamespace(
            id=1, processing_mode=mode, file=ContentFile(data, name=name)
        )

    def test_advanced_markdown_preserves_headings_and_table(self):
        for name in ("guide.md", "guide.markdown"):
            with self.subTest(name=name):
                parsed = DocumentParsingService().parse(self.file("advanced", name))
                self.assertEqual(parsed.parser, "docling")
                self.assertGreaterEqual(parsed.structure.sections, 2)
                self.assertEqual(parsed.structure.tables, 1)
                self.assertIn("Apple", parsed.text)

    def test_basic_markdown_never_selects_docling(self):
        with patch(
            "core.services.document_parsing_service.get_document_parsers"
        ) as select:
            parsed = DocumentParsingService().parse(self.file("basic", "guide.md"))
        select.assert_not_called()
        self.assertEqual(parsed.parser, "basic")

    def test_plain_text_and_csv_use_docling_in_advanced_mode(self):
        for name, data in (("guide.txt", MARKDOWN), ("sales.csv", CSV)):
            with self.subTest(name=name):
                parsed = DocumentParsingService().parse(
                    self.file("advanced", name, data)
                )
                self.assertEqual(parsed.parser, "docling")
                self.assertGreater(len(parsed.elements), 0)

    def test_json_keeps_the_basic_reader_in_advanced_mode(self):
        parsed = DocumentParsingService().parse(self.file("advanced", "data.json"))
        self.assertEqual(parsed.parser, "basic")

    def test_failed_docling_conversion_records_basic_fallback(self):
        parser = Mock(name="docling_parser")
        parser.name = "docling"
        parser.supports.return_value = True
        parser.parse.side_effect = RuntimeError("conversion failed")
        with patch(
            "core.services.document_parsers.get_docling_parser", return_value=parser
        ):
            parsed = DocumentParsingService().parse(self.file("advanced", "guide.md"))
        self.assertEqual(parsed.parser, "basic")
        self.assertEqual(parsed.fallback_from, "docling")
