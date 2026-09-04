import io

from django.test import SimpleTestCase
from PyPDF2 import PdfReader, PdfWriter

from core.services.document_parsers.pdf_outline import _walk


class PdfOutlineTests(SimpleTestCase):
    def test_extracts_chapter_and_numbered_section_destinations(self):
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        writer.add_outline_item("Chapter 3: Testing", 0)
        writer.add_outline_item("3.2 Test cases", 1)
        writer.write(output)
        output.seek(0)
        reader = PdfReader(output)

        targets = list(_walk(reader, reader.outline, level=1))

        self.assertIn(
            ("chapter", "3", 1), [(x.kind, x.key, x.page_no) for x in targets]
        )
        self.assertIn(
            ("section", "3.2", 2), [(x.kind, x.key, x.page_no) for x in targets]
        )
