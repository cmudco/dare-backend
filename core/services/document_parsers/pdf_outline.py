"""Native PDF bookmark extraction for reference-resolution fallbacks."""

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, List

from PyPDF2 import PdfReader

from core.services.document_parsers.headings import heading_number

logger = logging.getLogger(__name__)

CHAPTER = re.compile(r"^\s*chapter\s+(\d+)\b", re.I)
APPENDIX = re.compile(r"^\s*appendix\s+([A-Za-z])\b", re.I)


@dataclass(frozen=True)
class PdfOutlineTarget:
    kind: str
    key: str
    title: str
    page_no: int
    level: int


def extract_pdf_outline(file: Any) -> List[PdfOutlineTarget]:
    """Read useful bookmark anchors, returning an empty list on bad PDFs."""
    if not (file.file.name or "").lower().endswith(".pdf"):
        return []
    try:
        with file.file.open("rb") as handle:
            reader = PdfReader(handle)
            return list(_walk(reader, reader.outline or [], level=1))
    except Exception as error:
        logger.warning("Could not read PDF outline for file %s: %s", file.id, error)
        return []


def _walk(
    reader: PdfReader, items: Iterable[Any], level: int
) -> Iterable[PdfOutlineTarget]:
    for item in items:
        if isinstance(item, list):
            yield from _walk(reader, item, level + 1)
            continue
        title = str(getattr(item, "title", "") or "").strip()
        try:
            page_no = reader.get_destination_page_number(item) + 1
        except Exception:
            continue
        chapter = CHAPTER.match(title)
        appendix = APPENDIX.match(title)
        number = heading_number(title)
        if chapter:
            yield PdfOutlineTarget("chapter", chapter.group(1), title, page_no, level)
        if appendix:
            yield PdfOutlineTarget(
                "appendix", appendix.group(1).upper(), title, page_no, level
            )
        if number:
            yield PdfOutlineTarget("section", number, title, page_no, level)
