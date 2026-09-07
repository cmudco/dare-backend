from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from core.services.document_parsing_service import DocumentParsingService
from files.models import File


class ProcessingModeTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="mode@example.com", password="test"
        )
        self.client.force_authenticate(self.user)

    def upload(self, mode=None):
        data = {
            "files": SimpleUploadedFile(
                "notes.txt", b"Learning document modes", content_type="text/plain"
            ),
            "names": "notes.txt",
        }
        if mode is not None:
            data["processing_mode"] = mode
        with patch(
            "core.services.file_upload_service.FileUploadService.enqueue_processing"
        ):
            return self.client.post("/api/files/", data, format="multipart")

    def test_modes_persist_and_default_stays_advanced(self):
        for mode in ["basic", "advanced", None]:
            with self.subTest(mode=mode):
                response = self.upload(mode)
                self.assertEqual(response.status_code, 201, response.data)
                file = File.active_objects.get(pk=response.data[0]["id"])
                self.assertEqual(file.processing_mode, mode or "advanced")
                self.assertEqual(
                    response.data[0]["processing_mode"], mode or "advanced"
                )
                file.file.delete(save=False)

    def test_invalid_mode_creates_no_file(self):
        response = self.upload("legacy")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(File.active_objects.filter(user=self.user).exists())

    def test_upload_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.upload("basic").status_code, 401)

    def test_basic_bypasses_docling_on_every_parse(self):
        file = File(id=1, name="notes.pdf", processing_mode="basic")
        service = DocumentParsingService()
        with patch.object(service, "_read_bytes", return_value=b"pdf"), patch.object(
            service, "_filename", return_value="notes.pdf"
        ), patch.object(service, "_parsers_for") as advanced, patch(
            "core.services.document_parsers.legacy_parser.read_bytes_as_text",
            return_value="Text only",
        ):
            for _ in range(2):
                parsed = service.parse(file)
                self.assertEqual(parsed.text, "Text only")
                self.assertEqual(parsed.structure.pictures, 0)
                self.assertIsNone(parsed.fallback_from)
            advanced.assert_not_called()

    def test_advanced_keeps_registered_parser_path(self):
        file = File(id=1, processing_mode="advanced")
        service = DocumentParsingService()
        with patch.object(service, "_read_bytes", return_value=b"text"), patch.object(
            service, "_filename", return_value="notes.txt"
        ), patch.object(service, "_parsers_for", return_value=[]) as advanced:
            with self.assertRaisesRegex(Exception, "Could not parse"):
                service.parse(file)
            advanced.assert_called_once_with("notes.txt")
