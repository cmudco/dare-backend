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

from core.config.document_parsing import (CAPTION_LIMIT, ELEMENT_TEXT_LIMIT,
                                          FURNITURE_LABELS, HEADING_LABELS,
                                          MIN_CONTENT_CHARS, SECTION_LIMIT,
                                          TABLE_MARKDOWN_LIMIT, ElementKind,
                                          ElementLabel)


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
        return {
            "parser": self.parser,
            "duration_seconds": round(self.duration_seconds, 3),
            "counts": self.structure.to_dict(),
            "elements": [element.to_dict() for element in self.elements],
        }


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
