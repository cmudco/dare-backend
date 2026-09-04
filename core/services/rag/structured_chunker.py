"""Structure-aware chunking (rung 0 of the document map).

Cuts on the parser's elements instead of a flat string so every chunk knows
its pages, its section and the element range it covers. Pure: no ORM, no
network. Enrichment results arrive as the document-model dicts the
enrichment service already produces.
"""

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.config.document_parsing import ElementKind
from core.services.document_text_coverage import (
    MIN_RECOVERED_TEXT_CHARACTERS,
    missing_text_blocks,
)
from core.services.dtos.parsed_document_dto import ParsedDocument, ParsedElement

CHUNK_TEXT = "text"
CHUNK_TABLE = "table"
CHUNK_FIGURE = "figure"
CHUNK_PAGE = "page_transcription"
CHUNK_FLAT = "flat"
CHUNK_RECOVERED = "recovered_text"


@dataclass(frozen=True)
class StructuredChunk:
    """One source passage, its retrieval representation, and its location."""

    text: str
    element_kind: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_order: Optional[int] = None
    section: str = ""
    heading_path: Tuple[str, ...] = ()
    order_start: Optional[int] = None
    order_end: Optional[int] = None
    retrieval_text: str = ""

    @property
    def searchable_text(self) -> str:
        """Contextual text used for retrieval; ``text`` remains original content."""
        return self.retrieval_text or self.text


PATH_SEPARATOR = " > "
DEFAULT_HEADING_PREFIX_LIMIT = 500
MIN_CHUNK_SIZE = 200
MIN_BODY_CHARS = 80
MIN_TEXT_CHUNK_DIVISOR = 3
SPLIT_SEPARATORS = [
    "\n\n",
    "\n",
    " ",
    ".",
    ",",
    "\u200b",
    "\uff0c",
    "\u3001",
    "\uff0e",
    "\u3002",
    "",
]


def heading_prefix(path: Sequence[str], max_characters: int) -> str:
    """Compact a heading path into one retrieval-only prefix."""
    if not path or max_characters <= 0:
        return ""
    remaining = list(path)
    line = PATH_SEPARATOR.join(remaining)
    while len(remaining) > 1 and len(line) > max_characters:
        remaining.pop(0)
        line = PATH_SEPARATOR.join(remaining)
    if len(line) > max_characters:
        line = line[: max(max_characters - 1, 0)] + "…"
    return line + "\n"


def retrieval_text_for(
    body: str,
    path: Sequence[str],
    max_prefix_characters: int = DEFAULT_HEADING_PREFIX_LIMIT,
) -> str:
    """Add document location for ranking without changing the source passage."""
    return heading_prefix(path, max_prefix_characters) + body


@dataclass
class _Run:
    """The text run being accumulated under one heading."""

    path: Tuple[str, ...] = ()
    section_order: Optional[int] = None
    section: str = ""
    parts: List[str] = field(default_factory=list)
    orders: List[int] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n\n".join(self.parts)

    def add(self, element: ParsedElement, text: Optional[str]) -> None:
        if text:
            self.parts.append(text)
        self.orders.append(element.order)
        if element.page_no is not None:
            self.pages.append(element.page_no)


class StructuredChunker:
    """Elements plus enrichment results -> chunks that know where they live."""

    def __init__(self, chunk_size: int, overlap: int):
        self.chunk_size = max(int(chunk_size), MIN_CHUNK_SIZE)
        self.overlap = max(min(int(overlap), self.chunk_size // 2), 0)
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.overlap,
            length_function=len,
            separators=SPLIT_SEPARATORS,
        )

    def _splitter_for(self, budget: int) -> RecursiveCharacterTextSplitter:
        size = max(int(budget), MIN_BODY_CHARS)
        return RecursiveCharacterTextSplitter(
            chunk_size=size,
            chunk_overlap=min(self.overlap, size // 4),
            length_function=len,
            separators=SPLIT_SEPARATORS,
        )

    @property
    def minimum_text_size(self) -> int:
        """Soft floor for prose retrieval context, bounded by the hard maximum."""
        return min(
            self.chunk_size,
            max(MIN_CHUNK_SIZE, self.chunk_size // MIN_TEXT_CHUNK_DIVISOR),
        )

    def chunk(
        self,
        parsed: ParsedDocument,
        document_model: Optional[Dict[str, Any]] = None,
        fallback_text: str = "",
    ) -> List[StructuredChunk]:
        model = document_model or {}
        element_results = {
            int(item["order"]): item["enrichment"]
            for item in model.get("elements", [])
            if item.get("order") is not None
            and isinstance(item.get("enrichment"), dict)
        }
        page_results = {
            int(row["page_no"]): row
            for row in model.get("page_enrichments", [])
            if row.get("page_no") is not None
        }
        transcribed = {
            page
            for page, row in page_results.items()
            if self._searchable_page_result(row)
        }
        if not parsed.elements:
            if transcribed:
                return [
                    chunk
                    for page_no in sorted(transcribed)
                    for chunk in self._page_chunks(
                        page_no, page_results[page_no], (), (), None, ""
                    )
                ]
            return self._flat(fallback_text or parsed.embeddable_text)

        by_order = {element.order: element for element in parsed.elements}

        chunks: List[StructuredChunk] = []
        run = _Run()
        emitted_pages = set()

        for element in parsed.elements:
            path = self._path(element, by_order)
            section_order, section = self._context(element, by_order)

            if element.page_no in transcribed:
                if element.page_no not in emitted_pages:
                    self._flush(run, chunks)
                    run = _Run()
                    chunks.extend(
                        self._page_chunks(
                            element.page_no,
                            page_results[element.page_no],
                            parsed.elements,
                            path,
                            section_order,
                            section,
                        )
                    )
                    emitted_pages.add(element.page_no)
                continue
            if element.is_furniture:
                continue
            if element.kind == ElementKind.PICTURE:
                result = element_results.get(element.order) or {}
                if result.get("status") == "complete":
                    self._flush(run, chunks)
                    run = _Run()
                    chunks.append(
                        self._figure_chunk(
                            element, result, path, section_order, section
                        )
                    )
                continue
            if element.kind == ElementKind.TABLE and element.table_markdown:
                self._flush(run, chunks)
                run = _Run()
                chunks.extend(self._table_chunks(element, path, section_order, section))
                continue
            if element.is_heading:
                self._flush(run, chunks)
                run = _Run(path=path, section_order=section_order, section=section)
                run.add(element, None)
                continue
            text = element.text
            if not text:
                continue
            if run.path != path or run.section_order != section_order:
                self._flush(run, chunks)
                run = _Run(path=path, section_order=section_order, section=section)
            prefix_length = len(self._prefix(path))
            if len(text) > self.chunk_size - prefix_length:
                self._flush(run, chunks)
                run = _Run(path=path, section_order=section_order, section=section)
                body_splitter = self._splitter_for(self.chunk_size - prefix_length)
                for piece in body_splitter.split_text(text):
                    piece_run = _Run(
                        path=path, section_order=section_order, section=section
                    )
                    piece_run.add(element, piece)
                    self._flush(piece_run, chunks)
                continue
            if (
                run.parts
                and prefix_length + len(run.body) + 2 + len(text) > self.chunk_size
            ):
                carry = self._tail(run.body)
                self._flush(run, chunks)
                run = _Run(path=path, section_order=section_order, section=section)
                if (
                    carry
                    and prefix_length + len(carry) + 2 + len(text) <= self.chunk_size
                ):
                    run.parts.append(carry)
            run.add(element, text)

        self._flush(run, chunks)
        for page_no in sorted(transcribed - emitted_pages):
            chunks.extend(
                self._page_chunks(
                    page_no, page_results[page_no], parsed.elements, (), None, ""
                )
            )
        contextualized = self._contextualize_small_text_chunks(chunks)
        return self._append_recovered_text(
            contextualized,
            parsed,
            fallback_text or parsed.embeddable_text,
        )

    # ----- helpers -----

    def _flat(self, text: str) -> List[StructuredChunk]:
        if not text or not text.strip():
            return []
        return [
            StructuredChunk(text=piece, element_kind=CHUNK_FLAT)
            for piece in self._splitter.split_text(text)
        ]

    def _prefix(self, path: Tuple[str, ...]) -> str:
        return heading_prefix(path, self.chunk_size // 3)

    def _flush(self, run: _Run, chunks: List[StructuredChunk]) -> None:
        if not run.parts:
            return
        body = run.body
        prefix = self._prefix(run.path)
        chunks.append(
            StructuredChunk(
                text=body,
                element_kind=CHUNK_TEXT,
                page_start=min(run.pages) if run.pages else None,
                page_end=max(run.pages) if run.pages else None,
                section_order=run.section_order,
                section=run.section,
                heading_path=run.path,
                order_start=min(run.orders) if run.orders else None,
                order_end=max(run.orders) if run.orders else None,
                retrieval_text=prefix + body if prefix else "",
            )
        )

    def _tail(self, body: str) -> str:
        if self.overlap <= 0 or len(body) <= self.overlap:
            return ""
        tail = body[-self.overlap :]
        cut = tail.find(" ")
        return tail[cut + 1 :].strip() if cut >= 0 else tail.strip()

    def _contextualize_small_text_chunks(
        self, chunks: List[StructuredChunk]
    ) -> List[StructuredChunk]:
        """Give undersized prose enough neighboring context for retrieval.

        The document map and citations keep the parser's exact section-sized
        ``text``. Only ``retrieval_text`` borrows nearby prose. This is the
        same safety principle as Docling's hybrid peer merging, without
        collapsing several headings into one misleading map node.
        """
        floor = self.minimum_text_size
        contextualized: List[StructuredChunk] = []
        for index, chunk in enumerate(chunks):
            if chunk.element_kind != CHUNK_TEXT or len(chunk.text) >= floor:
                contextualized.append(chunk)
                continue

            positions = [index]
            left, right = index - 1, index + 1
            while len(self._joined_search_text(chunks, positions, index)) < floor:
                candidates = []
                if left >= 0 and self._is_local_text_neighbor(chunk, chunks[left]):
                    candidates.append(left)
                if right < len(chunks) and self._is_local_text_neighbor(
                    chunk, chunks[right]
                ):
                    candidates.append(right)
                if not candidates:
                    break

                # Prefer the neighbor that adds the most useful context while
                # still fitting. Ties keep reading order deterministic.
                fitting = [
                    position
                    for position in candidates
                    if len(
                        self._joined_search_text(chunks, positions + [position], index)
                    )
                    <= self.chunk_size
                ]
                if not fitting:
                    break
                chosen = max(
                    fitting,
                    key=lambda position: (len(chunks[position].text), -position),
                )
                positions.append(chosen)
                if chosen == left:
                    left -= 1
                else:
                    right += 1

            retrieval_text = self._joined_search_text(chunks, positions, index)
            contextualized.append(
                replace(
                    chunk,
                    retrieval_text=(
                        retrieval_text
                        if retrieval_text != chunk.searchable_text
                        else chunk.retrieval_text
                    ),
                )
            )
        return contextualized

    def _append_recovered_text(
        self,
        chunks: List[StructuredChunk],
        parsed: ParsedDocument,
        fallback_text: str,
    ) -> List[StructuredChunk]:
        """Add retrieval-only passages for content absent from the structure.

        Docling's hierarchy is valuable, but the complete extraction remains
        the content safety net.  The comparison is paragraph based and uses
        word n-grams so harmless whitespace, Markdown and chunk-boundary
        differences do not create duplicate vectors.  Recovered passages have
        no structural coordinates and are filtered out of the document Map by
        the ingestion service.
        """
        if not fallback_text.strip():
            return chunks

        # Heading prefixes are already indexed alongside their body. Counting
        # them prevents bare duplicate headings from crowding out real evidence.
        source_parts = [chunk.searchable_text for chunk in chunks]
        for element in parsed.elements:
            # Intentionally excluded running furniture must not be recovered;
            # all other coverage is measured against emitted evidence.
            if element.is_furniture:
                source_parts.append(element.text)
        recovered: List[StructuredChunk] = []
        seen = set()
        paragraphs = missing_text_blocks(
            fallback_text,
            source_parts,
            minimum_characters=MIN_RECOVERED_TEXT_CHARACTERS,
        )
        for paragraph in paragraphs:
            pieces = (
                self._splitter.split_text(paragraph)
                if len(paragraph) > self.chunk_size
                else [paragraph]
            )
            for piece in pieces:
                if piece in seen:
                    continue
                seen.add(piece)
                recovered.append(
                    StructuredChunk(text=piece, element_kind=CHUNK_RECOVERED)
                )
        return chunks + recovered

    @staticmethod
    def _joined_search_text(
        chunks: Sequence[StructuredChunk],
        positions: Sequence[int],
        primary: Optional[int] = None,
    ) -> str:
        ordered = sorted(set(positions))
        if primary is not None and primary in ordered:
            # The passage being represented must lead. If every sibling emits
            # the same sorted neighbourhood, their vectors and reranker inputs
            # become indistinguishable even though their source bodies differ.
            ordered = [
                primary,
                *(position for position in ordered if position != primary),
            ]
        return "\n\n".join(chunks[position].searchable_text for position in ordered)

    @staticmethod
    def _is_local_text_neighbor(
        target: StructuredChunk, candidate: StructuredChunk
    ) -> bool:
        if candidate.element_kind != CHUNK_TEXT:
            return False
        same_section = bool(target.heading_path) and (
            target.heading_path == candidate.heading_path
        )
        same_page = (
            target.page_start is not None
            and candidate.page_start is not None
            and target.page_start <= (candidate.page_end or candidate.page_start)
            and candidate.page_start <= (target.page_end or target.page_start)
        )
        return same_section or same_page

    @staticmethod
    def _heading_chain(
        element: ParsedElement, by_order: Dict[int, ParsedElement]
    ) -> List[ParsedElement]:
        chain: List[ParsedElement] = []
        current = (
            element if element.is_heading else by_order.get(element.parent_order or -1)
        )
        seen = set()
        while current is not None and current.order not in seen:
            seen.add(current.order)
            chain.append(current)
            current = by_order.get(current.parent_order or -1)
        chain.reverse()
        return chain

    def _path(
        self, element: ParsedElement, by_order: Dict[int, ParsedElement]
    ) -> Tuple[str, ...]:
        chain = self._heading_chain(element, by_order)
        if chain:
            return tuple(item.text for item in chain if item.text)
        return tuple(
            item["text"] for item in element.heading_context if item.get("text")
        )

    def _context(
        self, element: ParsedElement, by_order: Dict[int, ParsedElement]
    ) -> Tuple[Optional[int], str]:
        chain = self._heading_chain(element, by_order)
        if chain:
            return chain[-1].order, chain[-1].text
        if element.heading_context:
            last = element.heading_context[-1]
            return last.get("order"), last.get("text", "")
        return None, element.section or ""

    def _page_chunks(
        self,
        page_no: int,
        result: Dict[str, Any],
        elements: Sequence[ParsedElement],
        path: Tuple[str, ...],
        section_order: Optional[int],
        section: str,
    ) -> List[StructuredChunk]:
        parts: List[str] = []
        summary = str(result.get("summary") or "").strip()
        transcription = str(result.get("transcription_markdown") or "").strip()
        if summary:
            parts.append(f"[Machine-generated page description: {summary}]")
        if transcription:
            parts.append(transcription)
        uncertainty = str(result.get("uncertainty") or "").strip()
        warning = (
            f"[Transcription uncertainty: {uncertainty}]\n\n" if uncertainty else ""
        )
        if not parts:
            return []
        orders = [element.order for element in elements if element.page_no == page_no]
        text = "\n\n".join(parts)
        prefix = self._prefix(path)
        available = max(self.chunk_size - len(prefix) - len(warning), MIN_BODY_CHARS)
        pieces = (
            self._splitter_for(available).split_text(text)
            if len(text) > available
            else [text]
        )
        return [
            StructuredChunk(
                text=warning + piece,
                element_kind=CHUNK_PAGE,
                page_start=page_no,
                page_end=page_no,
                section_order=section_order,
                section=section,
                heading_path=path,
                order_start=min(orders) if orders else None,
                order_end=max(orders) if orders else None,
                retrieval_text=prefix + warning + piece if prefix else "",
            )
            for piece in pieces
        ]

    def _figure_chunk(
        self,
        element: ParsedElement,
        result: Dict[str, Any],
        path,
        section_order,
        section,
    ) -> StructuredChunk:
        parts = []
        if element.caption:
            parts.append(f"Figure: {element.caption}")
        description = str(result.get("description") or "").strip()
        visible = str(result.get("visible_text") or "").strip()
        if description:
            parts.append(f"[Machine-generated figure description: {description}]")
        if visible:
            parts.append(f"Visible text in figure: {visible}")
        uncertainty = str(result.get("uncertainty") or "").strip()
        if uncertainty:
            parts.insert(0, f"[Figure uncertainty: {uncertainty}]")
        body = "\n\n".join(parts)
        prefix = self._prefix(path)
        return StructuredChunk(
            text=body,
            element_kind=CHUNK_FIGURE,
            page_start=element.page_no,
            page_end=element.page_no,
            section_order=section_order,
            section=section,
            heading_path=path,
            order_start=element.order,
            order_end=element.order,
            retrieval_text=prefix + body if prefix else "",
        )

    def _table_chunks(
        self, element: ParsedElement, path, section_order, section
    ) -> List[StructuredChunk]:
        caption = f"Table: {element.caption}\n\n" if element.caption else ""
        markdown = element.table_markdown or ""
        prefix = self._prefix(path)
        pieces: List[str]
        available = max(self.chunk_size - len(prefix) - len(caption), MIN_BODY_CHARS)
        if len(markdown) <= available:
            pieces = [markdown]
        else:
            lines = markdown.splitlines()
            header, rows = lines[:2], lines[2:]
            if not rows:
                pieces = self._splitter.split_text(markdown)
            else:
                budget = available
                pieces, current = [], []
                header_text = "\n".join(header)
                row_budget = max(budget - len(header_text) - 1, MIN_BODY_CHARS)
                for row in rows:
                    if len(row) > row_budget:
                        if current:
                            pieces.append("\n".join(header + current))
                            current = []
                        pieces.extend(
                            f"{header_text}\n{piece}"
                            for piece in self._splitter_for(row_budget).split_text(row)
                        )
                        continue
                    if (
                        current
                        and sum(len(line) + 1 for line in header + current + [row])
                        > budget
                    ):
                        pieces.append("\n".join(header + current))
                        current = []
                    current.append(row)
                if current:
                    pieces.append("\n".join(header + current))
        return [
            StructuredChunk(
                text=caption + piece,
                element_kind=CHUNK_TABLE,
                page_start=element.page_no,
                page_end=element.page_no,
                section_order=section_order,
                section=section,
                heading_path=path,
                order_start=element.order,
                order_end=element.order,
                retrieval_text=prefix + caption + piece if prefix else "",
            )
            for piece in pieces
        ]

    @staticmethod
    def _searchable_page_result(result: Dict[str, Any]) -> bool:
        if result.get("status") != "complete" or result.get("kind") == "blank_page":
            return False
        return bool(
            str(result.get("summary") or "").strip()
            or str(result.get("transcription_markdown") or "").strip()
        )
