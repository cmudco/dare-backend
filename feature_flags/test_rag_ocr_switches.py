from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from feature_flags.constants import ENABLE_OCR, ENABLE_RAG
from feature_flags.models import FeatureFlag
from files.models import File


class RagOcrSwitchTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="switches@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _turn_off(key):
        FeatureFlag.objects.filter(key=key).update(default_enabled=False)

    def _upload(self):
        return self.client.post(
            "/api/files/",
            {"files": [SimpleUploadedFile("note.txt", b"hello")], "names": ["note"]},
            format="multipart",
        )

    def test_uploads_and_graphs_are_refused_when_rag_is_off(self):
        self._turn_off(ENABLE_RAG)

        self.assertEqual(self._upload().status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.get("/api/knowledge-graphs/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        # Existing files stay listable so they can be exported or deleted.
        self.assertEqual(self.client.get("/api/files/").status_code, status.HTTP_200_OK)

    def test_transcription_approval_is_refused_when_ocr_is_off(self):
        self._turn_off(ENABLE_OCR)
        file = File.active_objects.create(
            user=self.user,
            name="scan.pdf",
            file=SimpleUploadedFile("scan.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )

        response = self.client.post(
            f"/api/files/{file.id}/approve-ocr/", {"pageLimit": 5}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
