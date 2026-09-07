"""Application service for one document ingestion or OCR continuation run."""

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple
from uuid import uuid4

from django.db.models import Q
from django.utils import timezone

from config import env
from core.services.document_ocr_workflow_service import (
    DocumentOcrPlan,
    DocumentOcrWorkflowService,
)
from core.services.document_processor import DocumentProcessor
from core.services.document_text_sanitizer import sanitize_document_text
from core.services.dtos.parsed_document_dto import ParsedDocument
from core.services.file_processing_journey import FileProcessingJourney
from files.constants import (
    DocumentOcrStatus,
    DocumentReprocessingAction,
    FileProcessingStage,
    FileStatus,
)
from files.models import DocumentOcrRequest, File

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentIngestionCommand:
    file_id: int
    chunk_size: Optional[int] = None
    overlap_size: Optional[int] = None
    expected_job_id: Optional[str] = None
    reprocessing_action: Optional[str] = None
    processing_mode: Optional[str] = None
    previous_status: Optional[int] = None

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

    # Only a request the user has never approved has nothing to index: no page
    # was ever transcribed and the spend is still the user's to authorize.
    # Every other state owns a persisted parse worth rebuilding from.
    SKIPPED_OCR_STATES = {DocumentOcrStatus.AWAITING_APPROVAL}

    # Docling already ran for these; the parse is on the request.
    PERSISTED_PARSE_STATES = {
        DocumentOcrStatus.APPROVED,
        DocumentOcrStatus.COMPLETE,
        DocumentOcrStatus.PARTIAL,
    }

    # Transcription has stopped — finished, or partway with the rest deferred.
    # A run in these states re-indexes the stored page transcriptions and buys
    # no new vision calls, so the request itself must come out untouched.
    REUSED_TRANSCRIPTION_STATES = {
        DocumentOcrStatus.COMPLETE,
        DocumentOcrStatus.PARTIAL,
    }

    def process(self, command: DocumentIngestionCommand) -> Optional[int]:
        token = uuid4()
        now = timezone.now()
        expired = now - timedelta(seconds=env.DOCUMENT_OCR_JOB_TIMEOUT_SECONDS + 60)
        candidates = File.active_objects.filter(pk=command.file_id)
        if command.expected_job_id:
            candidates = candidates.filter(
                job_id=command.expected_job_id, status=FileStatus.PROCESSING
            )
        acquired = candidates.filter(
            Q(ingestion_token__isnull=True) | Q(ingestion_started_at__lt=expired)
        ).update(ingestion_token=token, ingestion_started_at=now)
        if not acquired:
            logger.info(
                "Document ingestion already running or file unavailable: %s",
                command.file_id,
            )
            return None
        try:
            return self._process(command)
        finally:
            File._base_manager.filter(pk=command.file_id, ingestion_token=token).update(
                ingestion_token=None, ingestion_started_at=None
            )

    def _process(self, command: DocumentIngestionCommand) -> Optional[int]:
        try:
            file = File.active_objects.select_related("user").get(id=command.file_id)
        except File.DoesNotExist:
            return None

        if file.is_media:
            return None

        snapshot = (
            {
                name: deepcopy(getattr(file, name))
                for name in (
                    "processing_mode",
                    "parser_name",
                    "document_model",
                    "extracted_text",
                    "page_count",
                    "pages_without_text",
                    "status",
                    "processing_stage",
                    "error_message",
                )
            }
            if command.reprocessing_action
            else {}
        )
        previous_generation = (
            file.index_generation if command.reprocessing_action else None
        )
        retry_images = (
            command.reprocessing_action == DocumentReprocessingAction.RETRY_IMAGES
        )
        reparse = command.reprocessing_action == DocumentReprocessingAction.REPARSE
        ocr_request = DocumentOcrRequest.objects.filter(file=file).first()
        ocr_snapshot = (
            {
                field.attname: deepcopy(getattr(ocr_request, field.attname))
                for field in DocumentOcrRequest._meta.concrete_fields
            }
            if reparse and ocr_request
            else None
        )
        if (
            not retry_images
            and not reparse
            and ocr_request
            and ocr_request.status in self.SKIPPED_OCR_STATES
        ):
            logger.info(
                "Skipping file %s because OCR is in state %s",
                file.id,
                ocr_request.status,
            )
            return None

        reusing_transcriptions = bool(
            not reparse
            and ocr_request
            and ocr_request.status in self.REUSED_TRANSCRIPTION_STATES
        )

        journey = FileProcessingJourney(file)
        journey.begin_attempt()
        file.status = FileStatus.PROCESSING
        file.processing_stage = FileProcessingStage.PARSING
        file.error_message = None
        file.save(update_fields=["status", "processing_stage", "error_message"])

        try:
            if reparse:
                file.processing_mode = command.processing_mode
                file.save(update_fields=["processing_mode"])
                DocumentOcrRequest.objects.filter(file=file).delete()
                ocr_request = None
            processor = DocumentProcessor()
            if retry_images:
                with journey.stage("parsing") as stage:
                    parsed = ParsedDocument.from_persisted(
                        file.extracted_text or "", file.document_model
                    )
                    stage.skip(
                        "Reused document structure to retry failed image descriptions."
                    )
                continuing = True
            else:
                parsed, continuing = self._load_or_parse(
                    file, ocr_request, processor, journey
                )

            ocr_workflow = DocumentOcrWorkflowService()
            if reusing_transcriptions or retry_images:
                # `prepare` would pause this run — a complete request has no
                # remaining pages and a partial one waits on the user — and
                # would rewrite the request's page limit on the way. There is
                # nothing left to approve, so plan a run that transcribes no
                # page and leaves the request exactly as the user left it.
                ocr_plan = DocumentOcrPlan(should_pause=False, page_limit=0)
            else:
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
                continue_existing_enrichment=continuing or reusing_transcriptions,
                **({"retry_failed_images": True} if retry_images else {}),
                **({"require_vectors": True} if command.reprocessing_action else {}),
            )

            ocr_status = None
            if not reusing_transcriptions and not retry_images:
                enrichment = (file.document_model or {}).get("enrichment", {})
                ocr_status = ocr_workflow.finish(
                    file,
                    enrichment.get(
                        "processed_pages", enrichment.get("transcribed_pages", 0)
                    ),
                )
            file.status, file.error_message = self._resolve_status(file, vector_count)
            file.processing_stage = FileProcessingStage.COMPLETE
            file.save(
                update_fields=[
                    "status",
                    "processing_stage",
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
            if command.reprocessing_action:
                file.refresh_from_db(fields=["index_generation"])
                if file.index_generation != previous_generation:
                    File.active_objects.filter(pk=file.pk).update(
                        status=FileStatus.PROCESSED,
                        error_message="Search index updated, but processing status could not be finalized.",
                    )
                    raise
                snapshot["status"] = command.previous_status
                snapshot["processing_stage"] = FileProcessingStage.COMPLETE
                snapshot["error_message"] = (
                    "Reprocessing failed; the previous document and search index were retained."
                )
                File.active_objects.filter(
                    pk=file.pk, ingestion_token=file.ingestion_token
                ).update(**snapshot)
                if reparse:
                    DocumentOcrRequest.objects.filter(file_id=file.pk).delete()
                    if ocr_snapshot:
                        DocumentOcrRequest.objects.create(**ocr_snapshot)
                logger.exception("Document reprocessing failed for file %s", file.id)
                raise
            file.status = FileStatus.FAILED
            file.error_message = sanitize_document_text(str(error))
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
        # `parsed_text` is null only when no parse was ever stored; an empty
        # string is a real parse of a document whose text all lives in the
        # page transcriptions, so it must not fall through to Docling.
        if (
            ocr_request
            and ocr_request.status in DocumentIngestionService.PERSISTED_PARSE_STATES
            and ocr_request.parsed_text is not None
            and bool((file.document_model or {}).get("chunk_elements_lossless"))
        ):
            with journey.stage("parsing") as stage:
                parsed = ParsedDocument.from_persisted(
                    ocr_request.parsed_text, file.document_model
                )
                parsed = processor.parsing_service.attach_pdf_recovery_text(
                    file, parsed
                )
                processor._record_parse_details(stage, parsed)
                stage.skip(
                    "Reused the saved Docling parse; completed pages were not parsed again."
                )
            return parsed, bool(ocr_request.processed_pages)

        previous_model = deepcopy(file.document_model or {})
        with journey.stage("parsing") as stage:
            parsed = processor.parse_file(file)
            processor._record_parse_details(stage, parsed)
        if ocr_request and previous_model:
            DocumentIngestionService._restore_enrichment_results(file, previous_model)
            ocr_request.parsed_text = parsed.text
            ocr_request.save(update_fields=["parsed_text", "updated_at"])
        return parsed, False

    @staticmethod
    def _restore_enrichment_results(file: File, previous: dict) -> None:
        """Carry paid vision results across a repair parse of a legacy model."""
        fresh = dict(file.document_model or {})
        if previous.get("page_enrichments"):
            fresh["page_enrichments"] = deepcopy(previous["page_enrichments"])
        if previous.get("enrichment"):
            fresh["enrichment"] = deepcopy(previous["enrichment"])
        by_order = {
            item.get("order"): deepcopy(item["enrichment"])
            for item in previous.get("elements", [])
            if item.get("order") is not None
            and isinstance(item.get("enrichment"), dict)
        }
        for item in fresh.get("elements", []):
            enrichment = by_order.get(item.get("order"))
            if enrichment:
                item["enrichment"] = enrichment
        file.document_model = fresh
        file.save(update_fields=["document_model", "updated_at"])

    @staticmethod
    def _resolve_status(file: File, vector_count: int) -> Tuple[int, Optional[str]]:
        if vector_count > 0:
            return FileStatus.PROCESSED, None

        enrichment = (file.document_model or {}).get("enrichment", {})
        processed_pages = enrichment.get(
            "processed_pages", enrichment.get("transcribed_pages", 0)
        )
        if (
            file.pages_without_text
            and processed_pages >= file.pages_without_text
            and enrichment.get("blank_pages", 0) >= file.pages_without_text
        ):
            return FileStatus.FAILED, (
                "Every scanned page was blank, so there is no searchable content "
                "to embed."
            )

        if file.page_count and file.pages_without_text >= file.page_count:
            return FileStatus.NEEDS_OCR, (
                f"All {file.page_count} pages are scanned images with no readable "
                "text and vision transcription was unavailable. Nothing was embedded, "
                "so this file cannot answer questions yet."
            )

        return FileStatus.FAILED, (
            "No text could be extracted from this file, so nothing was embedded."
        )
