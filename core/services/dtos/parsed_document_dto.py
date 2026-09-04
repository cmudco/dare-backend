"""
Parsed Document DTOs

The structured result of parsing an uploaded file. A parser returns the flat
text we embed *and* the document model behind it: every element in reading
order, carrying its label, page, position and — for pictures — the caption the
parser linked to it.

The reading-order index matters downstream: a description generated for a
picture can be re-inserted exactly where the picture sat rather than appended
at the end of the document.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.config.document_parsing import (
    CAPTION_LIMIT,
    ELEMENT_TEXT_LIMIT,
    FURNITURE_LABELS,
    HEADING_LABELS,
    MAX_STORED_ELEMENTS,
    MIN_CONTENT_CHARS,
    SECTION_LIMIT,
    TABLE_MARKDOWN_LIMIT,
    ElementKind,
    ElementLabel,
)


@dataclass(frozen=True)
class BoundingBox:
    """Element position on its page, as fractions of page width and height.

    Page-relative rather than absolute so the frontend can overlay boxes on a
    page rendered at any size, and so the values survive re-rendering at a
    different DPI.
    """

    left: float
    top: float
    width: float
    height: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "left": round(self.left, 4),
            "top": round(self.top, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
        }


@dataclass(frozen=True)
class ParsedElement:
    """One element of the document model, positioned in reading order."""

    order: int
    kind: str
    label: str
    page_no: Optional[int] = None
    text: str = ""
    section: Optional[str] = None
    caption: Optional[str] = None
    table_markdown: Optional[str] = None
    bbox: Optional[BoundingBox] = None
    tree_depth: int = 0
    heading_context: Tuple[Dict[str, Any], ...] = ()
    classifications: Tuple[Dict[str, Any], ...] = ()
    content_sha256: Optional[str] = None
    level: Optional[int] = None
    parent_order: Optional[int] = None
    number: Optional[str] = None
    chunk_index: Optional[int] = None

    @property
    def is_furniture(self) -> bool:
        """Running head, footer or footnote — dropped before chunking."""
        return self.label in FURNITURE_LABELS

    @property
    def is_heading(self) -> bool:
        return self.label in HEADING_LABELS

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for storage on the File row and for the API."""
        payload: Dict[str, Any] = {
            "order": self.order,
            "kind": self.kind,
            "label": self.label,
            "page_no": self.page_no,
        }
        if self.text:
            payload["text"] = self.text[:ELEMENT_TEXT_LIMIT]
        if self.section:
            payload["section"] = self.section[:SECTION_LIMIT]
        if self.caption:
            payload["caption"] = self.caption[:CAPTION_LIMIT]
        if self.table_markdown:
            payload["table_markdown"] = self.table_markdown[:TABLE_MARKDOWN_LIMIT]
        if self.bbox:
            payload["bbox"] = self.bbox.to_dict()
        if self.tree_depth:
            payload["tree_depth"] = self.tree_depth
        if self.heading_context:
            payload["heading_context"] = list(self.heading_context)
        if self.classifications:
            payload["classifications"] = list(self.classifications)
        if self.content_sha256:
            payload["content_sha256"] = self.content_sha256
        if self.level is not None:
            payload["level"] = self.level
        if self.parent_order is not None:
            payload["parent_order"] = self.parent_order
        if self.number:
            payload["number"] = self.number
        if self.chunk_index is not None:
            payload["chunk_index"] = self.chunk_index
        return payload

    def to_chunk_dict(self) -> Dict[str, Any]:
        """Compact lossless form used to rebuild structure-aware chunks.

        The public structure view can cap and trim element previews, but a
        later re-index must retain every paragraph and complete table. Visual
        coordinates and classifications are intentionally omitted here.
        """
        payload: Dict[str, Any] = {
            "order": self.order,
            "kind": self.kind,
            "label": self.label,
            "page_no": self.page_no,
        }
        for key, value in (
            ("text", self.text),
            ("caption", self.caption),
            ("table_markdown", self.table_markdown),
        ):
            if value:
                payload[key] = value
        if self.level is not None:
            payload["level"] = self.level
        if self.parent_order is not None:
            payload["parent_order"] = self.parent_order
        if self.number:
            payload["number"] = self.number
        return payload


@dataclass(frozen=True)
class DocumentStructure:
    """Headline counts, mirrored onto File columns so the UI can summarise
    a file without loading the whole document model."""

    pages: int = 0
    sections: int = 0
    tables: int = 0
    pictures: int = 0
    pages_without_text: int = 0
    content_chars: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "pages": self.pages,
            "sections": self.sections,
            "tables": self.tables,
            "pictures": self.pictures,
            "pages_without_text": self.pages_without_text,
            "content_chars": self.content_chars,
        }


@dataclass(frozen=True)
class ParsedDocument:
    """Everything a parser recovered from one file."""

    text: str = ""
    elements: Tuple[ParsedElement, ...] = ()
    structure: DocumentStructure = field(default_factory=DocumentStructure)
    parser: str = "unknown"
    duration_seconds: float = 0.0
    recovery_text: str = ""
    fallback_from: Optional[str] = None
    fallback_reason: Optional[str] = None

    @property
    def has_text(self) -> bool:
        """Whether the parse recovered real content.

        Deliberately measured from the elements rather than from ``text``.
        A markdown export of a scanned PDF is not empty — it is a run of
        ``<!-- image -->`` placeholders and empty table pipes, which is enough
        characters to look like success while carrying nothing at all.
        """
        return self.structure.content_chars >= MIN_CONTENT_CHARS

    @property
    def is_page_based(self) -> bool:
        """True for formats that paginate (PDF). DOCX and spreadsheets do not."""
        return self.structure.pages > 0

    @property
    def needs_ocr(self) -> bool:
        """Parsed cleanly, but there was no text to recover.

        This is the scanned-document case: every page is an image, so nothing
        can be embedded. Reporting it as success is what previously let pages
        of client scans sit in the library answering no questions at all.
        """
        if not self.is_page_based:
            return False
        return (
            not self.has_text
            or self.structure.pages_without_text >= self.structure.pages
        )

    @property
    def embeddable_text(self) -> str:
        """Text worth embedding — empty when the parse recovered no content.

        Stops placeholder scaffolding from being chunked and stored as though
        it were document text.
        """
        return self.text if self.has_text else ""

    def outline(self) -> List[Dict[str, Any]]:
        """Headings only, for a compact table of contents."""
        return [
            {"order": element.order, "page_no": element.page_no, "text": element.text}
            for element in self.elements
            if element.is_heading and element.text
        ]

    def to_dict(self) -> Dict[str, Any]:
        """The document model as stored on ``File.document_model``."""
        stored = list(self.elements[:MAX_STORED_ELEMENTS])
        stored_orders = {element.order for element in stored}
        # Keep the row bounded for ordinary body text, but never cut off the
        # outline or the visual/table anchors that the Map and enrichment
        # layers depend on. Large books commonly cross the body-text cap long
        # before their final chapters.
        stored.extend(
            element
            for element in self.elements[MAX_STORED_ELEMENTS:]
            if element.order not in stored_orders
            and (
                element.is_heading
                or element.kind in {ElementKind.TABLE, ElementKind.PICTURE}
            )
        )
        payload = {
            "parser": self.parser,
            "duration_seconds": round(self.duration_seconds, 3),
            "counts": self.structure.to_dict(),
            "elements": [element.to_dict() for element in stored],
            "elements_truncated": len(self.elements) > MAX_STORED_ELEMENTS,
            "elements_stored": len(stored),
        }
        if self.fallback_from:
            payload["parser_fallback"] = {
                "from": self.fallback_from,
                "reason": self.fallback_reason or "Unknown parser failure",
            }
        # ``elements`` is a bounded UI representation: individual text fields
        # are deliberately shortened by ``ParsedElement.to_dict``.  Chunking
        # must never rebuild from those previews, even for a small document,
        # because a 401-character paragraph would otherwise silently lose its
        # tail on an OCR continuation or re-index.  Keep the complete, compact
        # chunking representation private in the stored model; serializers
        # expose only ``elements`` to the frontend.
        payload["chunk_elements"] = [
            element.to_chunk_dict() for element in self.elements
        ]
        payload["chunk_elements_lossless"] = True
        return payload

    @classmethod
    def from_persisted(
        cls, text: str, payload: Optional[Dict[str, Any]]
    ) -> "ParsedDocument":
        """Rehydrate the parser result stored on a File row.

        Enrichment adds fields to the stored model over time; this method only
        reads the parser-owned contract so continuation runs are forward-safe.
        """
        payload = payload or {}
        counts = payload.get("counts") or {}
        structure = DocumentStructure(
            pages=int(counts.get("pages") or 0),
            sections=int(counts.get("sections") or 0),
            tables=int(counts.get("tables") or 0),
            pictures=int(counts.get("pictures") or 0),
            pages_without_text=int(counts.get("pages_without_text") or 0),
            content_chars=int(counts.get("content_chars") or 0),
        )

        display_by_order = {
            int(item.get("order") or 0): item for item in payload.get("elements") or []
        }
        elements = []
        for chunk_item in payload.get("chunk_elements") or []:
            # The compact chunk representation owns complete text.  Merge in
            # preview-only annotations such as bounding boxes, classifications
            # and the last written chunk index when they are available.
            item = {
                **display_by_order.get(int(chunk_item.get("order") or 0), {}),
                **chunk_item,
            }
            bbox_payload = item.get("bbox") or None
            bbox = None
            if bbox_payload:
                bbox = BoundingBox(
                    left=float(bbox_payload.get("left") or 0),
                    top=float(bbox_payload.get("top") or 0),
                    width=float(bbox_payload.get("width") or 0),
                    height=float(bbox_payload.get("height") or 0),
                )
            elements.append(
                ParsedElement(
                    order=int(item.get("order") or 0),
                    kind=str(item.get("kind") or ElementKind.TEXT),
                    label=str(item.get("label") or ElementLabel.TEXT),
                    page_no=(
                        int(item["page_no"])
                        if item.get("page_no") is not None
                        else None
                    ),
                    text=str(item.get("text") or ""),
                    section=item.get("section"),
                    caption=item.get("caption"),
                    table_markdown=item.get("table_markdown"),
                    bbox=bbox,
                    tree_depth=int(item.get("tree_depth") or 0),
                    heading_context=tuple(item.get("heading_context") or ()),
                    classifications=tuple(item.get("classifications") or ()),
                    content_sha256=item.get("content_sha256"),
                    level=(
                        int(item["level"]) if item.get("level") is not None else None
                    ),
                    parent_order=(
                        int(item["parent_order"])
                        if item.get("parent_order") is not None
                        else None
                    ),
                    number=item.get("number") or None,
                    chunk_index=(
                        int(item["chunk_index"])
                        if item.get("chunk_index") is not None
                        else None
                    ),
                )
            )

        return cls(
            text=text or "",
            elements=tuple(elements),
            structure=structure,
            parser=str(payload.get("parser") or "unknown"),
            duration_seconds=float(payload.get("duration_seconds") or 0),
            fallback_from=(payload.get("parser_fallback") or {}).get("from"),
            fallback_reason=(payload.get("parser_fallback") or {}).get("reason"),
        )


def text_only_document(
    text: str, parser: str, duration_seconds: float = 0.0
) -> ParsedDocument:
    """Wrap flat text from a parser that has no notion of structure.

    Used by the legacy fallback parser so that every caller sees the same
    ``ParsedDocument`` shape regardless of which parser ran.
    """
    elements: Tuple[ParsedElement, ...] = ()
    if text:
        elements = (
            ParsedElement(
                order=1, kind=ElementKind.TEXT, label=ElementLabel.TEXT, text=text
            ),
        )
    return ParsedDocument(
        text=text or "",
        elements=elements,
        structure=DocumentStructure(content_chars=len((text or "").strip())),
        parser=parser,
        duration_seconds=duration_seconds,
    )
