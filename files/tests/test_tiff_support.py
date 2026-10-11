"""TIFF uploads become PDF documents; images headed for a model become PNG."""

import base64
import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from core.services.file_upload_service import FileUploadService
from core.services.image_formats import to_provider_data_url, to_provider_image
from files.constants import FileStatus


def tiff_bytes(pages=1, mode="RGB"):
    frames = [Image.new(mode, (40, 30), color=index * 60) for index in range(pages)]
    output = BytesIO()
    frames[0].save(output, "TIFF", save_all=True, append_images=frames[1:])
    return output.getvalue()


class ProviderImageTests(SimpleTestCase):
    def test_tiff_becomes_png_and_supported_types_pass_through(self):
        png, mime_type = to_provider_image(tiff_bytes(), "image/tiff")
        self.assertEqual(mime_type, "image/png")
        self.assertEqual(Image.open(BytesIO(png)).format, "PNG")

        jpeg = b"\xff\xd8 not decoded"
        self.assertEqual(to_provider_image(jpeg, "image/jpeg"), (jpeg, "image/jpeg"))

    def test_undecodable_images_are_left_for_the_provider_to_skip(self):
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
        self.assertEqual(
            to_provider_image(svg, "image/svg+xml"), (svg, "image/svg+xml")
        )

    def test_data_url_conversion(self):
        tiff_url = "data:image/tiff;base64," + base64.b64encode(tiff_bytes()).decode()
        self.assertTrue(to_provider_data_url(tiff_url).startswith("data:image/png;"))
        png_url = "data:image/png;base64,AAAA"
        self.assertEqual(to_provider_data_url(png_url), png_url)


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="tiff-tests-")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class TiffUploadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(shutil.rmtree, TEST_MEDIA_ROOT, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="tiff-owner@example.com", password="x"
        )

    def upload(self, name, data, content_type="image/tiff"):
        uploaded = SimpleUploadedFile(name, data, content_type=content_type)
        with patch.object(FileUploadService, "enqueue_processing"):
            return FileUploadService.create_file_instance(uploaded, name, self.user)

    def test_multi_page_tiff_is_stored_as_a_pdf_document(self):
        file = self.upload("scan.tif", tiff_bytes(pages=3, mode="1"))

        self.assertEqual(file.name, "scan.pdf")
        self.assertEqual(file.file_type, "application/pdf")
        self.assertFalse(file.is_media)
        self.assertEqual(file.status, FileStatus.PROCESSING)
        with file.file.open("rb") as stored:
            self.assertEqual(
                fitz.open(stream=stored.read(), filetype="pdf").page_count, 3
            )

    def test_unreadable_tiff_fails_with_a_readable_error(self):
        file = self.upload("broken.tiff", b"II*\x00 not really a tiff")

        self.assertEqual(file.status, FileStatus.FAILED)
        self.assertEqual(file.error_message, "This TIFF image could not be read.")

    def test_chat_tiff_attachment_is_saved_as_png(self):
        data_url = "data:image/tiff;base64," + base64.b64encode(tiff_bytes()).decode()
        file = FileUploadService.save_base64_image(
            base64_data=data_url,
            filename="photo.tiff",
            mime_type="image/tiff",
            user=self.user,
        )

        self.assertEqual((file.name, file.file_type), ("photo.png", "image/png"))
        self.assertTrue(file.is_media)
