from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from files.constants import DocumentOcrStatus, FileStatus
from files.models import DocumentOcrRequest, File


class DocumentOcrApprovalApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="ocr-owner@example.com", password="pw"
        )
        self.other = get_user_model().objects.create_user(
            email="ocr-other@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="scan.pdf",
            file=SimpleUploadedFile("scan.pdf", b"%PDF-test"),
            file_type="application/pdf",
            status=FileStatus.NEEDS_OCR,
            page_count=140,
            pages_without_text=140,
        )
        self.ocr_request = DocumentOcrRequest.objects.create(
            file=self.file,
            status=DocumentOcrStatus.AWAITING_APPROVAL,
            detected_pages=140,
            page_limit=10,
            max_page_limit=100,
            estimated_cost_per_page=Decimal("0.001"),
            model_identifier="gemini-vision",
            chunk_size=1500,
            overlap_size=180,
        )
        self.url = f"/api/files/{self.file.id}/approve-ocr/"

    @patch("files.services.document_ocr_approval_service.enqueue")
    def test_owner_can_approve_a_bounded_page_count(self, enqueue):
        enqueue.return_value.id = "ocr-job"
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.url, {"pageLimit": 50}, format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.ocr_request.refresh_from_db()
        self.file.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.APPROVED)
        self.assertEqual(self.ocr_request.page_limit, 50)
        self.assertEqual(self.file.status, FileStatus.PROCESSING)
        self.assertEqual(self.file.job_id, "ocr-job")
        enqueue.assert_called_once()

    @patch("files.services.document_ocr_approval_service.resolve_vision_model")
    @patch("files.services.document_ocr_approval_service.enqueue")
    def test_owner_can_pick_the_vision_model_for_the_run(self, enqueue, resolve):
        enqueue.return_value.id = "ocr-job"
        resolve.return_value = SimpleNamespace(
            model=SimpleNamespace(identifier="claude-haiku-4-5"),
            estimated_cost_per_page=Decimal("0.004"),
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.url,
            {"pageLimit": 10, "modelIdentifier": "claude-haiku-4-5"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        resolve.assert_called_once_with(self.user, "claude-haiku-4-5")
        self.ocr_request.refresh_from_db()
        self.assertEqual(self.ocr_request.model_identifier, "claude-haiku-4-5")
        self.assertEqual(self.ocr_request.estimated_cost_per_page, Decimal("0.004"))
        self.assertEqual(response.data["ocr"]["model_identifier"], "claude-haiku-4-5")

    @patch("files.services.document_ocr_approval_service.resolve_vision_model")
    def test_model_outside_the_wallet_is_rejected(self, resolve):
        resolve.return_value = SimpleNamespace(
            model=SimpleNamespace(identifier="gemini-vision"),
            estimated_cost_per_page=Decimal("0.001"),
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.url,
            {"pageLimit": 10, "modelIdentifier": "text-only-model"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.ocr_request.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.AWAITING_APPROVAL)
        self.assertEqual(self.ocr_request.model_identifier, "gemini-vision")

    @patch("files.services.document_ocr_approval_service.enqueue")
    def test_partial_run_can_continue_with_additional_pages(self, enqueue):
        enqueue.return_value.id = "continuation-job"
        self.ocr_request.status = DocumentOcrStatus.PARTIAL
        self.ocr_request.processed_pages = 10
        self.ocr_request.save(update_fields=["status", "processed_pages"])
        self.file.status = FileStatus.PROCESSED
        self.file.save(update_fields=["status"])
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.url, {"pageLimit": 25}, format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.ocr_request.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.APPROVED)
        self.assertEqual(self.ocr_request.page_limit, 25)
        self.assertEqual(response.data["ocr"]["remaining_pages"], 130)

    @patch("files.services.document_ocr_approval_service.enqueue")
    def test_same_run_cannot_be_started_twice(self, enqueue):
        enqueue.return_value.id = "ocr-job"
        self.client.force_authenticate(user=self.user)

        first = self.client.post(self.url, {"pageLimit": 10}, format="json")
        second = self.client.post(self.url, {"pageLimit": 10}, format="json")

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(enqueue.call_count, 1)

    @patch(
        "files.services.document_ocr_approval_service.enqueue",
        side_effect=RuntimeError("redis unavailable"),
    )
    def test_queue_failure_restores_partial_state(self, _enqueue):
        self.ocr_request.status = DocumentOcrStatus.PARTIAL
        self.ocr_request.processed_pages = 10
        self.ocr_request.save(update_fields=["status", "processed_pages"])
        self.file.status = FileStatus.PROCESSED
        self.file.save(update_fields=["status"])
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.url, {"pageLimit": 10}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.ocr_request.refresh_from_db()
        self.file.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.PARTIAL)
        self.assertEqual(self.file.status, FileStatus.PROCESSED)

    def test_page_count_cannot_exceed_the_server_cap(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.url, {"pageLimit": 101}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.ocr_request.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.AWAITING_APPROVAL)

    def test_another_user_cannot_approve_the_request(self):
        self.client.force_authenticate(user=self.other)

        response = self.client.post(self.url, {"pageLimit": 10}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.ocr_request.refresh_from_db()
        self.assertEqual(self.ocr_request.status, DocumentOcrStatus.AWAITING_APPROVAL)
