from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core.services.file_upload_service import FileUploadService


class FileUploadValidationTests(SimpleTestCase):
    def test_markdown_is_accepted_under_any_browser_mime_type(self):
        for content_type in (
            "text/markdown",
            "text/x-markdown",
            "application/octet-stream",
            "",
        ):
            uploaded = SimpleUploadedFile(
                "notes.md", b"# Notes", content_type=content_type
            )

            is_valid, error = FileUploadService.validate_file(uploaded, "notes.md")

            self.assertTrue(is_valid, content_type)
            self.assertIsNone(error)

    def test_unknown_extension_falls_back_to_mime_type(self):
        allowed = SimpleUploadedFile("report", b"x", content_type="application/pdf")
        rejected = SimpleUploadedFile(
            "archive.zip", b"x", content_type="application/zip"
        )

        self.assertEqual(
            FileUploadService.validate_file(allowed, "report"), (True, None)
        )
        self.assertEqual(
            FileUploadService.validate_file(rejected, "archive.zip"),
            (False, "File type application/zip not allowed"),
        )
