"""Transactional approval boundary for initial and continued OCR runs."""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from django_rq import enqueue

from core.services.vision_model_service import (
    VisionModelNotOffered,
    select_vision_model,
)
from files.constants import DocumentOcrStatus, FileProcessingStage, FileStatus
from files.models import DocumentOcrRequest, File


class DocumentOcrApprovalError(Exception):
    pass


class DocumentOcrNotFound(DocumentOcrApprovalError):
    pass


class DocumentOcrInvalidState(DocumentOcrApprovalError):
    pass


class DocumentOcrPageLimitError(DocumentOcrApprovalError):
    pass


class DocumentOcrModelError(DocumentOcrApprovalError):
    pass


class DocumentOcrQueueError(DocumentOcrApprovalError):
    pass


@dataclass(frozen=True)
class DocumentOcrApprovalCommand:
    file_id: int
    user_id: int
    page_limit: int
    model_identifier: str = ""


class DocumentOcrApprovalService:
    ALLOWED_STATES = {
        DocumentOcrStatus.AWAITING_APPROVAL,
        DocumentOcrStatus.PARTIAL,
    }

    def start(self, command: DocumentOcrApprovalCommand) -> File:
        with transaction.atomic():
            try:
                file = File.active_objects.select_for_update().get(
                    id=command.file_id, user_id=command.user_id
                )
                ocr_request = DocumentOcrRequest.objects.select_for_update().get(
                    file=file
                )
            except (File.DoesNotExist, DocumentOcrRequest.DoesNotExist) as error:
                raise DocumentOcrNotFound(
                    "This file does not have a transcription request."
                ) from error

            if ocr_request.status not in self.ALLOWED_STATES:
                raise DocumentOcrInvalidState(
                    "This transcription run was already started or is not resumable."
                )

            remaining_pages = max(
                ocr_request.detected_pages - ocr_request.processed_pages, 0
            )
            selectable_pages = min(remaining_pages, ocr_request.max_page_limit)
            if not 1 <= command.page_limit <= selectable_pages:
                raise DocumentOcrPageLimitError(
                    f"pageLimit must be between 1 and {selectable_pages}."
                )

            if command.model_identifier:
                try:
                    route = select_vision_model(file.user, command.model_identifier)
                except VisionModelNotOffered as error:
                    raise DocumentOcrModelError(str(error)) from error
                ocr_request.model_identifier = route.model.identifier
                ocr_request.estimated_cost_per_page = route.estimated_cost_per_page

            previous_ocr_status = ocr_request.status
            approved_at = timezone.now()
            ocr_request.page_limit = command.page_limit
            ocr_request.status = DocumentOcrStatus.APPROVED
            ocr_request.approved_at = approved_at
            ocr_request.save(
                update_fields=[
                    "page_limit",
                    "status",
                    "approved_at",
                    "model_identifier",
                    "estimated_cost_per_page",
                    "updated_at",
                ]
            )

            file.status = FileStatus.PROCESSING
            file.processing_stage = FileProcessingStage.ENRICHING
            file.error_message = None
            file.save(update_fields=["status", "processing_stage", "error_message"])

        try:
            from files.tasks import process_file_embeddings

            job = enqueue(
                process_file_embeddings,
                file.id,
                ocr_request.chunk_size,
                ocr_request.overlap_size,
            )
        except Exception as error:
            self._restore_after_queue_failure(
                file.id, ocr_request.id, previous_ocr_status
            )
            raise DocumentOcrQueueError(
                "Could not queue transcription. Please try again."
            ) from error

        file.job_id = job.id
        file.save(update_fields=["job_id"])
        return file

    @staticmethod
    def _restore_after_queue_failure(
        file_id: int, ocr_request_id: int, previous_ocr_status: str
    ) -> None:
        with transaction.atomic():
            DocumentOcrRequest.objects.filter(
                id=ocr_request_id, status=DocumentOcrStatus.APPROVED
            ).update(
                status=previous_ocr_status,
                approved_at=None,
                updated_at=timezone.now(),
            )
            File.active_objects.filter(id=file_id).update(
                status=(
                    FileStatus.NEEDS_OCR
                    if previous_ocr_status == DocumentOcrStatus.AWAITING_APPROVAL
                    else FileStatus.PROCESSED
                ),
                processing_stage=FileProcessingStage.COMPLETE,
                error_message=None,
            )
