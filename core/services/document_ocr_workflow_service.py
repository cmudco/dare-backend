"""Cost-aware approval gate for scanned-page vision transcription."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from config import env
from core.services.dtos.parsed_document_dto import ParsedDocument
from core.services.vision_model_service import resolve_vision_model
from files.constants import DocumentOcrStatus
from files.models import DocumentOcrRequest, File


@dataclass(frozen=True)
class DocumentOcrPlan:
    should_pause: bool
    page_limit: Optional[int] = None


class DocumentOcrWorkflowService:
    """Create and advance the persisted OCR plan for one parsed PDF."""

    def prepare(
        self,
        file: File,
        parsed: ParsedDocument,
        chunk_size: Optional[int] = None,
        overlap_size: Optional[int] = None,
    ) -> DocumentOcrPlan:
        detected_pages = int(parsed.structure.pages_without_text or 0)
        if not self._applies(file, parsed, detected_pages):
            return DocumentOcrPlan(should_pause=False)

        auto_limit = max(int(env.DOCUMENT_OCR_AUTO_PAGE_LIMIT), 1)
        max_limit = max(int(env.DOCUMENT_OCR_MAX_PAGE_LIMIT), auto_limit)
        selectable_pages = min(detected_pages, max_limit)

        request, created = DocumentOcrRequest.objects.get_or_create(
            file=file,
            defaults={
                "detected_pages": detected_pages,
                "page_limit": min(auto_limit, selectable_pages),
                "max_page_limit": max_limit,
                "chunk_size": chunk_size,
                "overlap_size": overlap_size,
                "parsed_text": parsed.text,
            },
        )

        # A model chosen at approval time outranks the user's default; either
        # falls back to the wallet's recommendation when no longer offered.
        route = resolve_vision_model(
            file.user, request.model_identifier or file.user.vision_model
        )
        request.model_identifier = route.model.identifier if route else ""
        request.estimated_cost_per_page = (
            route.estimated_cost_per_page if route else Decimal("0")
        )

        if not created:
            request.detected_pages = detected_pages
            request.max_page_limit = max_limit
            if request.chunk_size is None:
                request.chunk_size = chunk_size
            if request.overlap_size is None:
                request.overlap_size = overlap_size
            if request.parsed_text is None:
                request.parsed_text = parsed.text

        if route is None:
            request.status = DocumentOcrStatus.UNAVAILABLE
            request.page_limit = 0
            request.save()
            return DocumentOcrPlan(should_pause=False, page_limit=0)

        if request.status == DocumentOcrStatus.UNAVAILABLE:
            request.status = DocumentOcrStatus.AWAITING_APPROVAL
            request.page_limit = min(auto_limit, selectable_pages)

        remaining_pages = max(detected_pages - int(request.processed_pages or 0), 0)
        selectable_pages = min(remaining_pages, max_limit)
        if not remaining_pages:
            request.status = DocumentOcrStatus.COMPLETE
            request.page_limit = 0
            request.save()
            return DocumentOcrPlan(should_pause=True, page_limit=0)

        if request.status == DocumentOcrStatus.PARTIAL:
            request.page_limit = min(auto_limit, selectable_pages)
            request.save()
            return DocumentOcrPlan(should_pause=True)

        if request.status == DocumentOcrStatus.AWAITING_APPROVAL:
            if detected_pages > auto_limit:
                request.page_limit = min(
                    request.page_limit or auto_limit, selectable_pages
                )
                request.save()
                return DocumentOcrPlan(should_pause=True)
            request.page_limit = detected_pages

        request.page_limit = min(max(int(request.page_limit or 0), 1), selectable_pages)
        request.status = DocumentOcrStatus.PROCESSING
        request.save()
        return DocumentOcrPlan(should_pause=False, page_limit=request.page_limit)

    @staticmethod
    def finish(file: File, transcribed_pages: int) -> Optional[str]:
        try:
            request = file.ocr_request
        except DocumentOcrRequest.DoesNotExist:
            return None

        request.processed_pages = max(int(transcribed_pages or 0), 0)
        if request.processed_pages <= 0:
            request.status = DocumentOcrStatus.UNAVAILABLE
        elif request.processed_pages < request.detected_pages:
            request.status = DocumentOcrStatus.PARTIAL
        else:
            request.status = DocumentOcrStatus.COMPLETE
        remaining_pages = max(request.detected_pages - request.processed_pages, 0)
        request.page_limit = min(
            max(int(env.DOCUMENT_OCR_AUTO_PAGE_LIMIT), 1),
            request.max_page_limit,
            remaining_pages,
        )
        request.save(
            update_fields=["processed_pages", "page_limit", "status", "updated_at"]
        )
        return request.status

    @staticmethod
    def _applies(file: File, parsed: ParsedDocument, detected_pages: int) -> bool:
        return bool(
            detected_pages
            and parsed.parser == "docling"
            and parsed.is_page_based
            and (file.file.name or "").lower().endswith(".pdf")
        )
