"""Application service for one document ingestion or OCR continuation run."""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from core.services.document_ocr_workflow_service import DocumentOcrWorkflowService
from core.services.document_processor import DocumentProcessor
from core.services.dtos.parsed_document_dto import ParsedDocument
from core.services.file_processing_journey import FileProcessingJourney
from files.constants import DocumentOcrStatus, FileProcessingStage, FileStatus
from files.models import DocumentOcrRequest, File

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentIngestionCommand:
    file_id: int
    chunk_size: Optional[int] = None
    overlap_size: Optional[int] = None

    @classmethod
    def from_raw(cls, file_id, chunk_size=None, overlap_size=None):
        return cls(
            file_id=int(file_id),
            chunk_size=cls._optional_int(chunk_size),
            overlap_size=cls._optional_int(overlap_size),
        )

    @staticmethod
    def _optional_int(value) -> Optional[int]:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class DocumentIngestionService:
    """Coordinate parse, enrichment, embedding and indexing with resumable OCR."""

    STOPPED_OCR_STATES = {
        DocumentOcrStatus.AWAITING_APPROVAL,
        DocumentOcrStatus.PARTIAL,
        DocumentOcrStatus.COMPLETE,
    }

    def process(self, command: DocumentIngestionCommand) -> Optional[int]:
        try:
            file = File.active_objects.select_related("user").get(id=command.file_id)
        except File.DoesNotExist:
            return None

        if file.is_media:
            return None

        ocr_request = DocumentOcrRequest.objects.filter(file=file).first()
        if ocr_request and ocr_request.status in self.STOPPED_OCR_STATES:
            logger.info(
                "Skipping file %s because OCR is in state %s",
                file.id,
                ocr_request.status,
            )
            return None

        journey = FileProcessingJourney(file)
        journey.begin_attempt()
        file.status = FileStatus.PROCESSING
        file.processing_stage = FileProcessingStage.PARSING
        file.error_message = None
        file.save(update_fields=["status", "processing_stage", "error_message"])

        try:
            processor = DocumentProcessor()
            parsed, continuing = self._load_or_parse(
                file, ocr_request, processor, journey
            )

            ocr_workflow = DocumentOcrWorkflowService()
            ocr_plan = ocr_workflow.prepare(
                file,
                parsed,
                chunk_size=command.chunk_size,
                overlap_size=command.overlap_size,
            )
            if ocr_plan.should_pause:
                request = file.ocr_request
                file.status = FileStatus.NEEDS_OCR
                file.processing_stage = FileProcessingStage.COMPLETE
                file.error_message = (
                    f"{request.detected_pages} scanned pages need vision transcription. "
                    "Review the estimated cost and choose how many pages to process."
                )
                file.save(update_fields=["status", "processing_stage", "error_message"])
                journey.complete_attempt(outcome="awaiting_ocr_approval")
                return None

            vector_count = processor.create_file_embeddings(
                file,
                command.chunk_size,
                command.overlap_size,
                journey=journey,
                parsed=parsed,
                ocr_page_limit=ocr_plan.page_limit,
                continue_existing_enrichment=continuing,
            )

            enrichment = (file.document_model or {}).get("enrichment", {})
            ocr_status = ocr_workflow.finish(
                file, enrichment.get("transcribed_pages", 0)
            )
            file.vector_db_source = file.user.vector_db
            file.status, file.error_message = self._resolve_status(file, vector_count)
            file.processing_stage = FileProcessingStage.COMPLETE
            file.save(
                update_fields=[
                    "status",
                    "processing_stage",
                    "vector_db_source",
                    "error_message",
                ]
            )
            outcome = (
                "ocr_partial"
                if ocr_status == DocumentOcrStatus.PARTIAL
                else file.get_status_display().lower()
            )
            journey.complete_attempt(outcome=outcome)
            return vector_count
        except Exception as error:
            journey.fail_attempt(error)
            file.status = FileStatus.FAILED
            file.error_message = str(error)
            file.save(update_fields=["status", "error_message"])
            logger.exception("Document ingestion failed for file %s", file.id)
            raise

    @staticmethod
    def _load_or_parse(
        file: File,
        ocr_request: Optional[DocumentOcrRequest],
        processor: DocumentProcessor,
        journey: FileProcessingJourney,
    ) -> Tuple[ParsedDocument, bool]:
        if (
            ocr_request
            and ocr_request.status == DocumentOcrStatus.APPROVED
            and ocr_request.parsed_text is not None
        ):
            with journey.stage("parsing") as stage:
                parsed = ParsedDocument.from_persisted(
                    ocr_request.parsed_text, file.document_model
                )
                processor._record_parse_details(stage, parsed)
                stage.skip(
                    "Reused the saved Docling parse; completed pages were not parsed again."
                )
            return parsed, bool(ocr_request.processed_pages)

        with journey.stage("parsing") as stage:
            parsed = processor.parse_file(file)
            processor._record_parse_details(stage, parsed)
        return parsed, False

    @staticmethod
    def _resolve_status(file: File, vector_count: int) -> Tuple[int, Optional[str]]:
        if vector_count > 0:
            return FileStatus.PROCESSED, None

        if file.page_count and file.pages_without_text >= file.page_count:
            return FileStatus.NEEDS_OCR, (
                f"All {file.page_count} pages are scanned images with no readable "
                "text and vision transcription was unavailable. Nothing was embedded, "
                "so this file cannot answer questions yet."
            )

        return FileStatus.FAILED, (
            "No text could be extracted from this file, so nothing was embedded."
        )
