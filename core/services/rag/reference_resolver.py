"""Resolve in-document pointers to their targets (rung 1 of the document map)."""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from core.config.document_parsing import ElementLabel
from core.services.document_parsers.headings import heading_number
from core.services.document_parsers.pdf_outline import PdfOutlineTarget
from core.services.dtos.parsed_document_dto import ParsedElement
from core.services.rag.reference_extractor import PointerMatch, extract_pointers
from core.services.rag.structured_chunker import CHUNK_FLAT, StructuredChunk


@dataclass(frozen=True)
class ResolvedReference:
    """A pointer found in one chunk, with the target it resolved to, if any."""

    source_chunk_index: int
    kind: str
    key: str
    raw_text: str
    target_order: Optional[int] = None
    target_chunk_index: Optional[int] = None

    @property
    def resolved(self) -> bool:
        return self.target_order is not None or self.target_chunk_index is not None


CAPTION_PATTERN = re.compile(r"^\s*(figure|fig\.|table)\s*(\d+(?:\.\d+)*)", re.I)
APPENDIX_PATTERN = re.compile(r"^\s*appendix\s*([A-Za-z])\b", re.I)


class ReferenceResolver:
    """Maps pointers to targets using the file's headings, captions and pages."""

    def __init__(
        self,
        elements: Sequence[ParsedElement],
        chunks: Sequence[StructuredChunk],
        outline_targets: Sequence[PdfOutlineTarget] = (),
    ):
        self._heading_by_number: Dict[str, int] = {}
        self._appendix_by_letter: Dict[str, int] = {}
        self._caption_order: Dict[Tuple[str, str], int] = {}
        for element in elements:
            if element.is_heading and element.text:
                number = element.number or heading_number(element.text)
                if number:
                    self._heading_by_number.setdefault(number, element.order)
                appendix = APPENDIX_PATTERN.match(element.text)
                if appendix:
                    self._appendix_by_letter.setdefault(
                        appendix.group(1).upper(), element.order
                    )
            caption = element.caption or (
                element.text if element.label == ElementLabel.CAPTION else ""
            )
            match = CAPTION_PATTERN.match(caption or "")
            if match:
                kind = (
                    "table" if match.group(1).lower().startswith("table") else "figure"
                )
                self._caption_order.setdefault((kind, match.group(2)), element.order)

        self._chunks = list(chunks)
        self._outline_pages: Dict[Tuple[str, str], int] = {}
        for target in outline_targets:
            self._outline_pages.setdefault((target.kind, target.key), target.page_no)
        self._chunk_by_order: Dict[int, int] = {}
        self._first_chunk_by_section: Dict[int, int] = {}
        for index, chunk in enumerate(self._chunks):
            if chunk.order_start is not None and chunk.order_end is not None:
                for order in range(chunk.order_start, chunk.order_end + 1):
                    self._chunk_by_order.setdefault(order, index)
            if chunk.section_order is not None:
                self._first_chunk_by_section.setdefault(chunk.section_order, index)

    def resolve(
        self, source_chunk_index: int, pointer: PointerMatch
    ) -> Optional[ResolvedReference]:
        target_order: Optional[int] = None
        target_chunk: Optional[int] = None
        if pointer.kind in ("section", "chapter"):
            target_order = self._heading_by_number.get(pointer.key)
        elif pointer.kind == "appendix":
            target_order = self._appendix_by_letter.get(pointer.key.upper())
        elif pointer.kind in ("figure", "table"):
            order = self._caption_order.get((pointer.kind, pointer.key))
            if order is not None:
                target_chunk = self._chunk_by_order.get(order)
                if target_chunk == source_chunk_index:
                    return None
        elif pointer.kind == "page":
            page = int(pointer.key)
            for index, chunk in enumerate(self._chunks):
                if (
                    chunk.page_start is not None
                    and chunk.page_end is not None
                    and chunk.page_start <= page <= chunk.page_end
                ):
                    target_chunk = index
                    break
        if target_order is not None and target_chunk is None:
            target_chunk = self._first_chunk_by_section.get(target_order)
            if target_chunk is None:
                target_chunk = self._chunk_by_order.get(target_order)
        if target_order is None and target_chunk is None:
            outline_page = self._outline_pages.get((pointer.kind, pointer.key))
            if outline_page is not None:
                target_chunk = self._chunk_for_page(outline_page)
        if target_chunk == source_chunk_index:
            return None
        return ResolvedReference(
            source_chunk_index=source_chunk_index,
            kind=pointer.kind,
            key=pointer.key,
            raw_text=pointer.raw_text,
            target_order=target_order,
            target_chunk_index=target_chunk,
        )

    def _chunk_for_page(self, page_no: int) -> Optional[int]:
        first_after: Optional[int] = None
        for index, chunk in enumerate(self._chunks):
            if (
                chunk.page_start is not None
                and chunk.page_end is not None
                and chunk.page_start <= page_no <= chunk.page_end
            ):
                return index
            if (
                first_after is None
                and chunk.page_start is not None
                and chunk.page_start > page_no
            ):
                first_after = index
        # Bookmark destinations are frequently chapter divider pages with no
        # extracted body element. In that case the first following chunk is
        # the chapter content the pointer intends.
        return first_after


def build_references(
    elements: Sequence[ParsedElement],
    chunks: Sequence[StructuredChunk],
    outline_targets: Sequence[PdfOutlineTarget] = (),
) -> List[ResolvedReference]:
    """Extract and resolve every pointer in every structured chunk."""
    resolver = ReferenceResolver(elements, chunks, outline_targets)
    references: List[ResolvedReference] = []
    seen = set()
    for index, chunk in enumerate(chunks):
        if chunk.element_kind == CHUNK_FLAT:
            continue
        for pointer in extract_pointers(chunk.text):
            reference = resolver.resolve(index, pointer)
            if reference is None:
                continue
            dedupe_key = (index, reference.kind, reference.key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            references.append(reference)
    return references
