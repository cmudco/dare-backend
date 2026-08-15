"""
Docling Document Parser

Parses PDF, Office and HTML documents into a ``ParsedDocument`` using Docling.

Docling returns a structured document rather than a string, which is what
makes the rest of the pipeline possible: tables survive as markdown grids
instead of collapsing into word soup, running heads are labelled so they can be
dropped before chunking, and every picture arrives with its page, its position
in reading order and the caption Docling linked to it.

OCR is deliberately off. On the archival scans in the client corpus every local
OCR engine returns confident nonsense, which chunks and embeds exactly like
real text and is therefore worse than recovering nothing. Scanned pages are
routed to ``FileStatus.NEEDS_OCR`` instead and handled by the vision layer.
"""

import hashlib
import io
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from core.config.document_parsing import (HEADING_CONTEXT_LIMIT,
                                          MIN_CHARS_PER_PAGE,
                                          PICTURE_CLASSIFICATION_TOP_K,
                                          ElementKind, ElementLabel)
from core.services.document_parsers.base import BaseDocumentParser
from core.services.document_parsers.constants import (DOCLING_EXTENSIONS,
                                                      PARSER_DOCLING)
from core.services.dtos.parsed_document_dto import (BoundingBox,
                                                    DocumentStructure,
                                                    ParsedDocument,
                                                    ParsedElement)

logger = logging.getLogger(__name__)

# Docling's picture marker in the markdown export, and the blank runs that
# removing it leaves behind.
IMAGE_PLACEHOLDER_PATTERN = re.compile(r"^[ \t]*<!--\s*image\s*-->[ \t]*$\n?", re.M)
BLANK_RUN_PATTERN = re.compile(r"\n{3,}")


class DoclingDocumentParser(BaseDocumentParser):
    """Structure-aware parser backed by Docling."""

    name = PARSER_DOCLING

    def __init__(self, converter: Optional[DocumentConverter] = None):
        self._converter = converter
        self._converter_was_injected = converter is not None
        self._classification_fallback_converter: Optional[DocumentConverter] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def supports(self, filename: str) -> bool:
        return (filename or "").lower().rsplit(".", 1)[-1] in DOCLING_EXTENSIONS

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        """Convert raw bytes into a ParsedDocument."""
        started = time.time()
        source = DocumentStream(name=filename, stream=io.BytesIO(data))
        try:
            document = self._get_converter().convert(source).document
        except Exception as error:
            if self._converter_was_injected:
                raise
            # Figure classification is useful routing, not a reason to lose
            # the entire Docling structure when its optional model cannot load
            # (for example, a first worker boot without Hugging Face access).
            logger.warning(
                "Docling conversion with figure classification failed for %s; "
                "retrying structure-only: %s",
                filename,
                error,
            )
            retry_source = DocumentStream(name=filename, stream=io.BytesIO(data))
            document = self._get_classification_fallback_converter().convert(
                retry_source
            ).document

        elements = self._build_elements(document)
        structure = self._build_structure(document, elements)

        return ParsedDocument(
            text=self._clean_markdown(document.export_to_markdown()),
            elements=tuple(elements),
            structure=structure,
            parser=self.name,
            duration_seconds=time.time() - started,
        )

    @staticmethod
    def _clean_markdown(markdown: str) -> str:
        """Strip picture placeholders out of the text we embed.

        Docling marks every picture with an ``<!-- image -->`` comment. That is
        useful as a position marker, but the position already lives in the
        document model, and leaving the comments in means an NTSB report whose
        first page carries four logos opens its first chunk with four
        placeholders and no content. The vision layer will fill these positions
        with real descriptions later.
        """
        without_placeholders = IMAGE_PLACEHOLDER_PATTERN.sub("", markdown)
        return BLANK_RUN_PATTERN.sub("\n\n", without_placeholders).strip()

    # ------------------------------------------------------------------
    # Element extraction
    # ------------------------------------------------------------------

    def _build_elements(self, document: Any) -> List[ParsedElement]:
        """Walk the document in reading order, one ParsedElement per item.

        Tracks the most recent heading so that every element knows which
        section it belongs to — that is what lets a picture description carry
        "under 'How awake brain mapping works'" as context.
        """
        elements: List[ParsedElement] = []
        section: Optional[str] = None
        headings: List[Dict[str, Any]] = []
        order = 0

        for item, tree_depth in document.iterate_items():
            order += 1
            label = str(getattr(item, "label", "") or ElementLabel.TEXT)
            page_no, bbox = self._provenance(item, document)

            if label in (ElementLabel.TITLE, ElementLabel.SECTION_HEADER):
                section = (getattr(item, "text", "") or "").strip() or section
                if section:
                    headings.append(
                        {"order": order, "page_no": page_no, "text": section}
                    )

            kind = self._kind_of(item)
            elements.append(
                ParsedElement(
                    order=order,
                    kind=kind,
                    label=label,
                    page_no=page_no,
                    text=(getattr(item, "text", "") or "").strip(),
                    section=section,
                    caption=self._caption_of(item, document),
                    table_markdown=self._table_markdown(item, document, kind),
                    bbox=bbox,
                    tree_depth=tree_depth,
                    heading_context=tuple(headings[-HEADING_CONTEXT_LIMIT:]),
                    classifications=self._classifications_of(item),
                    content_sha256=self._content_sha256(item, document, kind),
                )
            )

        return elements

    @staticmethod
    def _kind_of(item: Any) -> str:
        class_name = type(item).__name__
        if class_name.startswith("Picture"):
            return ElementKind.PICTURE
        if class_name.startswith("Table"):
            return ElementKind.TABLE
        return ElementKind.TEXT

    @staticmethod
    def _provenance(
        item: Any, document: Any
    ) -> Tuple[Optional[int], Optional[BoundingBox]]:
        """Page number and page-relative bounding box for one item.

        Docling reports boxes bottom-left origin in absolute points; the
        frontend overlays them on a top-left origin image, so they are
        converted and normalised here rather than in three call sites later.
        """
        provenance = getattr(item, "prov", None) or []
        if not provenance:
            return None, None

        first = provenance[0]
        page_no = getattr(first, "page_no", None)
        page = (getattr(document, "pages", None) or {}).get(page_no)
        bbox = getattr(first, "bbox", None)
        if page is None or bbox is None:
            return page_no, None

        try:
            width = page.size.width
            height = page.size.height
            top_left = bbox.to_top_left_origin(page_height=height)
            return page_no, BoundingBox(
                left=top_left.l / width,
                top=top_left.t / height,
                width=(top_left.r - top_left.l) / width,
                height=(top_left.b - top_left.t) / height,
            )
        except (AttributeError, ZeroDivisionError, TypeError) as error:
            logger.debug(f"Could not normalise bbox on page {page_no}: {error}")
            return page_no, None

    @staticmethod
    def _caption_of(item: Any, document: Any) -> Optional[str]:
        """Caption text Docling linked to a picture or table."""
        captions = getattr(item, "captions", None) or []
        texts: List[str] = []
        for reference in captions:
            try:
                texts.append(reference.resolve(document).text or "")
            except Exception as error:
                logger.debug(f"Could not resolve caption reference: {error}")
        joined = " ".join(text for text in texts if text).strip()
        return joined or None

    @staticmethod
    def _classifications_of(item: Any) -> Tuple[Dict[str, Any], ...]:
        """Top local Docling figure-classifier predictions, highest first."""
        classification = getattr(getattr(item, "meta", None), "classification", None)
        predictions = getattr(classification, "predictions", None) or []
        ordered = sorted(
            predictions,
            key=lambda prediction: getattr(prediction, "confidence", 0.0) or 0.0,
            reverse=True,
        )
        return tuple(
            {
                "label": str(getattr(prediction, "class_name", "unknown")),
                "confidence": round(float(prediction.confidence), 4),
            }
            for prediction in ordered[:PICTURE_CLASSIFICATION_TOP_K]
            if getattr(prediction, "confidence", None) is not None
        )

    @staticmethod
    def _content_sha256(item: Any, document: Any, kind: str) -> Optional[str]:
        """Stable hash of a classified crop, used by the enrichment cache."""
        if kind != ElementKind.PICTURE:
            return None
        try:
            image = item.get_image(document).convert("RGB")
        except (AttributeError, TypeError, ValueError):
            return None

        digest = hashlib.sha256(usedforsecurity=False)
        digest.update(f"{image.width}x{image.height}:RGB".encode("ascii"))
        digest.update(image.tobytes())
        return digest.hexdigest()

    @staticmethod
    def _table_markdown(item: Any, document: Any, kind: str) -> Optional[str]:
        if kind != ElementKind.TABLE:
            return None
        try:
            markdown = item.export_to_markdown(document)
        except TypeError:
            markdown = item.export_to_markdown()
        except Exception as error:
            logger.debug(f"Table markdown export failed: {error}")
            return None
        return markdown.strip() or None

    # ------------------------------------------------------------------
    # Structure counts
    # ------------------------------------------------------------------

    def _build_structure(
        self, document: Any, elements: List[ParsedElement]
    ) -> DocumentStructure:
        pages = getattr(document, "pages", None) or {}
        chars_by_page = self._content_chars_by_page(pages, elements)
        return DocumentStructure(
            pages=len(pages),
            sections=sum(1 for element in elements if element.is_heading),
            tables=len(getattr(document, "tables", None) or []),
            pictures=len(getattr(document, "pictures", None) or []),
            pages_without_text=sum(
                1 for count in chars_by_page.values() if count < MIN_CHARS_PER_PAGE
            ),
            content_chars=sum(
                self._element_content_length(element) for element in elements
            ),
        )

    @staticmethod
    def _element_content_length(element: ParsedElement) -> int:
        """Characters of real content one element contributes.

        Table content lives in the markdown grid rather than in ``text``, so a
        spreadsheet — where every sheet is one big table and no text elements
        exist at all — would otherwise read as entirely blank.
        """
        if element.table_markdown:
            return len(element.table_markdown.strip())
        return len(element.text.strip())

    @classmethod
    def _content_chars_by_page(
        cls, pages: Dict[int, Any], elements: List[ParsedElement]
    ) -> Dict[int, int]:
        """Content characters per page.

        A stray glyph from a stamp or a margin note is not content, so callers
        compare against a per-page threshold rather than "any text at all".
        """
        if not pages:
            return {}

        chars_by_page: Dict[int, int] = {page_no: 0 for page_no in pages}
        for element in elements:
            if element.page_no in chars_by_page:
                chars_by_page[element.page_no] += cls._element_content_length(element)
        return chars_by_page

    # ------------------------------------------------------------------
    # Converter
    # ------------------------------------------------------------------

    def _get_converter(self) -> DocumentConverter:
        """Build the converter once and reuse it.

        Docling loads a layout model on first conversion (~20s); holding the
        converter on the parser instance keeps that cost to once per worker
        process rather than once per file.
        """
        if self._converter is None:
            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=self._pdf_options()
                    )
                }
            )
        return self._converter

    def _get_classification_fallback_converter(self) -> DocumentConverter:
        if self._classification_fallback_converter is None:
            self._classification_fallback_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=self._pdf_options(classify_pictures=False)
                    )
                }
            )
        return self._classification_fallback_converter

    @staticmethod
    def _pdf_options(classify_pictures: bool = True) -> PdfPipelineOptions:
        options = PdfPipelineOptions()
        options.do_ocr = False
        options.do_table_structure = True
        options.table_structure_options.do_cell_matching = True
        options.do_picture_description = False
        options.do_picture_classification = classify_pictures
        options.picture_classification_options.engine_options.top_k = (
            PICTURE_CLASSIFICATION_TOP_K
        )
        # Persist crop pixels only for the lifetime of conversion so the local
        # classifier can run and we can derive a stable content hash. Pixels are
        # not written to the database; paid vision calls re-render on demand.
        options.generate_picture_images = classify_pictures
        options.generate_page_images = False
        return options
