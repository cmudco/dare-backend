from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services.file_readers import read_pdf


class PdfReaderTests(SimpleTestCase):
    @patch("core.services.file_readers.fitz.open")
    def test_native_pdf_reader_preserves_paragraph_blocks(self, open_pdf):
        page = SimpleNamespace(
            rect=SimpleNamespace(height=1000),
            get_text=lambda _mode: [
                (0, 200, 1, 300, "First paragraph.\n", 0, 0),
                (
                    0,
                    400,
                    1,
                    500,
                    "Second paragraph with the missing fact.\n",
                    1,
                    0,
                ),
            ],
        )
        document = MagicMock()
        document.__enter__.return_value = [page]
        open_pdf.return_value = document

        text = read_pdf(b"pdf")

        self.assertEqual(
            text,
            "First paragraph.\n\nSecond paragraph with the missing fact.",
        )
        open_pdf.assert_called_once_with(stream=b"pdf", filetype="pdf")

    @patch("core.services.file_readers.PyPDF2.PdfReader")
    @patch("core.services.file_readers.fitz.open", side_effect=RuntimeError("bad pdf"))
    def test_native_reader_failure_uses_pypdf2(self, _open_pdf, pdf_reader):
        pdf_reader.return_value.pages = [
            SimpleNamespace(extract_text=lambda: "Fallback page")
        ]

        self.assertEqual(read_pdf(b"pdf"), "Fallback page")

    @patch("core.services.file_readers.fitz.open")
    def test_repeated_margin_furniture_is_removed(self, open_pdf):
        def page(number, body):
            return SimpleNamespace(
                rect=SimpleNamespace(height=1000),
                get_text=lambda _mode: [
                    (0, 10, 1, 40, f"Report 2026 — Page {number} of 2", 0, 0),
                    (0, 200, 1, 300, body, 1, 0),
                ],
            )

        document = MagicMock()
        document.__enter__.return_value = [page(1, "Alpha"), page(2, "Beta")]
        open_pdf.return_value = document

        self.assertEqual(read_pdf(b"pdf"), "Alpha\n\nBeta")
