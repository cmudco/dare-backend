"""Assemble stage — turn ranked chunks into the cited context the model sees.

Applies a token (char) budget and tags each passage with an inline ``[S#]``
citation so the model can attribute claims (the audit's "citations saved but not
shown" gap). Pure formatting: persistence of citation snippets is left to the
caller via an optional hook, so this class has a single concern (rules.md §2).
"""

from typing import Callable, List, Optional

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

        used = 0
        kept = 0
        for chunk in chunks:
            block = self._format(kept + 1, chunk)
            if used + len(block) > budget and kept:
                break  # stay within the prompt budget
            used += len(block)
            kept += 1
            blocks.append(block)
            if on_keep is not None:
                on_keep(kept, chunk)
        return blocks

    def _format(self, n: int, chunk: RetrievedChunk) -> str:
        cap = int(setting("RAG_SNIPPET_CHAR_CAP", DEFAULT_SNIPPET_CAP))
        text = chunk.text
        if len(text) > cap:
            text = text[:cap].rstrip() + " …"
        if chunk.source_type == "library" and chunk.library is not None:
            header = (
                f"[S{n}] {chunk.library.name} - {chunk.source_ref} (shared library)"
            )
        else:
            header = f"[S{n}] {chunk.source_ref or chunk.file_name or 'document'}"
        return f"{header}:\n{text}"
