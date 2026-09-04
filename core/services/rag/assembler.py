"""Format ranked chunks as cited, size-bounded model context."""

from typing import Callable, Dict, List, Optional, Tuple

from core.services.rag.config import setting
from core.services.rag.dtos import Grounding, RetrievedChunk

DEFAULT_CHAR_BUDGET = 16000
DEFAULT_SNIPPET_CAP = 2000


class ContextAssembler:
    """Ranked chunks (+ grounding) -> ordered, [S#]-cited, budget-bounded blocks."""

    def assemble(
        self,
        chunks: List[RetrievedChunk],
        grounding: Optional[Grounding] = None,
        on_keep: Optional[Callable[[int, RetrievedChunk], None]] = None,
    ) -> List[str]:
        budget = int(setting("RAG_CONTEXT_CHAR_BUDGET", DEFAULT_CHAR_BUDGET))
        blocks: List[str] = []

        if grounding is not None and not grounding.answer_found:
            blocks.append(
                "[grounding] Retrieval confidence is low "
                f"(top score {grounding.top_score:.2f}). If the passages below do "
                "not answer the question, say it is not in the sources."
            )
        preamble = len(blocks)

        tags = {(c.file_id, c.chunk_index): i for i, c in enumerate(chunks, 1)}
        used = 0
        kept = 0
        for chunk in chunks:
            block = self._format(kept + 1, chunk, tags)
            if used + len(block) > budget and kept:
                break  # stay within the prompt budget
            used += len(block)
            kept += 1
            blocks.append(block)
            if on_keep is not None:
                on_keep(kept, chunk)

        # A hop whose source did not make the budget must not cite a tag the
        # model never sees.
        for offset, chunk in enumerate(chunks[:kept]):
            if chunk.via is None:
                continue
            source_file = chunk.via.source_file_id or chunk.file_id
            source_tag = tags.get((source_file, chunk.via.source_chunk_index))
            if source_tag is not None and source_tag > kept:
                blocks[preamble + offset] = self._format(offset + 1, chunk, {})
        return blocks

    def _format(
        self, n: int, chunk: RetrievedChunk, tags: Dict[Tuple[str, int], int]
    ) -> str:
        cap = int(setting("RAG_SNIPPET_CHAR_CAP", DEFAULT_SNIPPET_CAP))
        text = chunk.text
        if len(text) > cap:
            text = text[:cap].rstrip() + " …"
        if chunk.source_type == "library" and chunk.library is not None:
            header = (
                f"[S{n}] {chunk.library.name} - {chunk.source_ref} (shared library)"
            )
        else:
            name = chunk.source_ref or chunk.file_name or "document"
            header = f"[S{n}] {name}" + "".join(
                f" · {part}" for part in self._location(chunk)
            )
            if chunk.via is not None:
                source_file = chunk.via.source_file_id or chunk.file_id
                source_tag = tags.get((source_file, chunk.via.source_chunk_index))
                if chunk.via.kind == "entity":
                    # An entity hop's source chunk may live in a file that
                    # was not cited (e.g. it fell out of budget); "chunk 38"
                    # from another document is not a usable pointer, so drop
                    # the origin instead of naming a chunk the model can't see.
                    if source_tag is not None:
                        header += (
                            f' · shares "{chunk.via.raw_text}" with [S{source_tag}]'
                        )
                    else:
                        header += f' · shares "{chunk.via.raw_text}"'
                else:
                    origin = (
                        f"[S{source_tag}]"
                        if source_tag is not None
                        else f"chunk {chunk.via.source_chunk_index}"
                    )
                    header += f' · followed "{chunk.via.raw_text}" from {origin}'
        return f"{header}:\n{text}"

    @staticmethod
    def _location(chunk: RetrievedChunk) -> List[str]:
        parts: List[str] = []
        if chunk.page_start is not None:
            if chunk.page_end is None or chunk.page_end == chunk.page_start:
                parts.append(f"p. {chunk.page_start}")
            else:
                parts.append(f"pp. {chunk.page_start}–{chunk.page_end}")
        if chunk.section:
            parts.append(chunk.section)
        return parts
