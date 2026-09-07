"""Owner-scoped queue boundary for explicit document reprocessing."""

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_rq import enqueue

from core.services.document_parsers.constants import PARSER_DOCLING
from files.constants import DocumentReprocessingAction, FileProcessingStage, FileStatus
from files.models import File


def failed_image_count(file):
    return sum(
        element.get("enrichment", {}).get("kind") == "figure_description"
        and element.get("enrichment", {}).get("status") == "error"
        for element in (file.document_model or {}).get("elements", [])
    )


class ReprocessingUnavailable(Exception):
    pass


class ReprocessingQueueError(Exception):
    pass


@dataclass(frozen=True)
class DocumentReprocessingCommand:
    file_id: int
    user_id: int
    action: str
    processing_mode: Optional[str] = None


class DocumentReprocessingService:
    def start(self, command: DocumentReprocessingCommand) -> File:
        file_id, user_id = command.file_id, command.user_id
        action, processing_mode = command.action, command.processing_mode
        with transaction.atomic():
            file = File.active_objects.select_for_update().get(
                pk=file_id, user_id=user_id
            )
            if file.is_media or not file.file:
                raise ReprocessingUnavailable(
                    _("Only documents with a stored original can be reprocessed.")
                )
            if file.status == FileStatus.PROCESSING or file.ingestion_token:
                raise ReprocessingUnavailable(
                    _("This file already has processing in progress.")
                )
            if (
                action == DocumentReprocessingAction.RETRY_IMAGES
                and not failed_image_count(file)
            ):
                raise ReprocessingUnavailable(
                    _("This file has no failed image descriptions to retry.")
                )
            if action == DocumentReprocessingAction.RETRY_IMAGES and (
                file.parser_name != PARSER_DOCLING
                or not (file.document_model or {}).get("chunk_elements_lossless")
            ):
                raise ReprocessingUnavailable(
                    _(
                        "Reprocess this older document with Advanced before retrying images."
                    )
                )
            previous_status = file.status
            previous_stage = file.processing_stage
            previous_job = file.job_id
            job_id = str(uuid4())
            file.job_id = job_id
            file.status = FileStatus.PROCESSING
            file.processing_stage = FileProcessingStage.PARSING
            file.save(update_fields=["job_id", "status", "processing_stage"])
        try:
            # Tasks import ingestion services, so resolve this entrypoint after startup.
            from files.tasks import reprocess_document

            enqueue(
                reprocess_document,
                file.id,
                job_id,
                action,
                processing_mode,
                previous_status,
                job_id=job_id,
            )
        except Exception as error:
            File.active_objects.filter(pk=file.id, job_id=job_id).update(
                status=previous_status,
                processing_stage=previous_stage,
                job_id=previous_job,
            )
            raise ReprocessingQueueError(
                _("Could not queue reprocessing. Please try again.")
            ) from error
        return file
