"""
Document Crop Service

Cuts a single element out of the original document as an image, using the page
and bounding box the parser recorded.

Nothing is stored: the crop is rendered on demand from the file already on
disk. That keeps a 65-picture newsletter from multiplying into 65 extra blobs,
and it means the same call serves both the structure view and the vision layer
that will describe these regions later.
"""

import io
import logging
import threading
from typing import Any, Dict, Tuple

import pypdfium2 as pdfium

from files.models import File

logger = logging.getLogger(__name__)

# pdfium is not thread-safe, and Django's sync views run in a threadpool under
# ASGI — so a structure panel opening thirty figures at once puts thirty
# threads inside the same native library. That corrupts pdfium's heap during
# font enumeration and takes the whole worker process down with SIGTRAP, which
# is not something the request can catch or recover from.
#
# Every call into pdfium is therefore serialised. A crop takes ~30ms, so even a
# 65-picture document costs about two seconds in total, and the requests are
# already queued behind the browser's own per-origin connection limit.
_PDFIUM_LOCK = threading.Lock()

# Rendered at 2x so a half-page figure still reads at full width in the UI.
RENDER_SCALE = 2.0

# Crops are for display, not archival. A full-width photo encodes to hundreds of
# kilobytes as PNG, and a page of them would crawl; capping the width and using
# JPEG keeps a typical figure well under 100 KB while staying legible for the
# text inside a cropped table.
MAX_CROP_WIDTH = 1200
JPEG_QUALITY = 85
CROP_CONTENT_TYPE = "image/jpeg"

# Pad the crop slightly: parser boxes hug the ink, and a hairline of margin
# stops captions and figure borders from being sliced in half.
BLEED = 0.004


class ElementNotFound(Exception):
    """No element with that reading-order index, or it has no position."""


class DocumentCropService:
    """Renders a region of a document as an image."""

    def crop_element(self, file: File, order: int) -> bytes:
        """Render the element at the given reading-order index.

        Args:
            file: File to crop from
            order: Reading-order index of the element, as stored in
                ``document_model``

        Returns:
            Encoded image bytes (see CROP_CONTENT_TYPE)

        Raises:
            ElementNotFound: If the element is unknown, has no bounding box, or
                the file is not a format that can be rendered.
        """
        element = self._find_element(file, order)
        page_no = element.get("page_no")
        bbox = element.get("bbox")

        if not page_no or not bbox:
            raise ElementNotFound(
                f"Element {order} of file {file.id} has no position on a page"
            )

        if not (file.file.name or "").lower().endswith(".pdf"):
            raise ElementNotFound("Only PDF documents can be cropped")

        with file.file.open("rb") as handle:
            data = handle.read()

        return self._render(data, page_no, bbox)

    def render_page(self, file: File, page_no: int) -> bytes:
        """Render a complete PDF page for the scanned-page transcription lane."""
        if not (file.file.name or "").lower().endswith(".pdf"):
            raise ElementNotFound("Only PDF documents can be rendered")

        with file.file.open("rb") as handle:
            data = handle.read()
        return self._render_page(data, page_no)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_element(file: File, order: int) -> Dict[str, Any]:
        elements = (file.document_model or {}).get("elements", [])
        for element in elements:
            if element.get("order") == order:
                return element
        raise ElementNotFound(f"File {file.id} has no element {order}")

    @classmethod
    def _render(cls, data: bytes, page_no: int, bbox: Dict[str, float]) -> bytes:
        """Rasterise one page and cut the bounding box out of it.

        Held under ``_PDFIUM_LOCK`` for the whole open/render/close cycle:
        pdfium keeps process-global state (its font cache among it), so two
        threads overlapping anywhere in this sequence is enough to corrupt it.
        Encoding happens outside the lock — that is pure Pillow work.
        """
        with _PDFIUM_LOCK:
            document = pdfium.PdfDocument(io.BytesIO(data))
            try:
                if page_no < 1 or page_no > len(document):
                    raise ElementNotFound(f"Page {page_no} is outside this document")

                # page_no is 1-based in the document model, pypdfium2 is 0-based.
                page = document[page_no - 1]
                image = page.render(scale=RENDER_SCALE).to_pil()
                # Detach from pdfium before the document closes.
                image = image.copy()
            finally:
                document.close()

        return cls._encode(image.crop(cls._box(image.size, bbox)))

    @classmethod
    def _render_page(cls, data: bytes, page_no: int) -> bytes:
        """Rasterise a full page under the same pdfium safety lock as crops."""
        with _PDFIUM_LOCK:
            document = pdfium.PdfDocument(io.BytesIO(data))
            try:
                if page_no < 1 or page_no > len(document):
                    raise ElementNotFound(f"Page {page_no} is outside this document")
                image = document[page_no - 1].render(scale=RENDER_SCALE).to_pil().copy()
            finally:
                document.close()
        return cls._encode(image)

    @staticmethod
    def _box(
        size: Tuple[int, int], bbox: Dict[str, float]
    ) -> Tuple[int, int, int, int]:
        """Convert a page-relative bbox into pixel coordinates, clamped."""
        width, height = size
        left = max(0.0, bbox.get("left", 0.0) - BLEED)
        top = max(0.0, bbox.get("top", 0.0) - BLEED)
        right = min(1.0, left + bbox.get("width", 0.0) + BLEED * 2)
        bottom = min(1.0, top + bbox.get("height", 0.0) + BLEED * 2)
        return (
            int(left * width),
            int(top * height),
            max(int(right * width), int(left * width) + 1),
            max(int(bottom * height), int(top * height) + 1),
        )

    @staticmethod
    def _encode(image) -> bytes:
        if image.width > MAX_CROP_WIDTH:
            height = round(image.height * MAX_CROP_WIDTH / image.width)
            image = image.resize((MAX_CROP_WIDTH, max(height, 1)))

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return buffer.getvalue()
