"""
Retrieval persistence targets.

The RAG pipeline persists three things about a retrieval: citation
snippets, the retrieval trace, and the once-per-turn trace reset. Chat
persists them on a ``Message``; workflow steps on a ``WorkflowRunStep``.
A target wraps one of those hosts so ``run_document_search`` /
``run_library_search`` / the ``search_documents`` executor never care
which one they are writing to.

A target instance lives for one turn: ``begin_agentic_search`` clears the
trace exactly once even when the model searches repeatedly in a turn.
"""

import logging
from typing import Any

from core.services.llm_helpers.db_helpers import (
    save_document_snippet,
    save_library_snippet,
    save_retrieval_trace,
)
from core.services.rag.dtos import CitationCounter
from files.models import File
from workflows.models import WorkflowStepSnippet

logger = logging.getLogger(__name__)


class TransientRetrievalTarget:
    """Own citations when retrieval has no persisted chat or workflow host."""

    def __init__(self):
        self.citations = CitationCounter()
        self._agentic_reset_done = False

    def reset_trace(self):
        self.citations.count = 0

    def begin_agentic_search(self):
        if not self._agentic_reset_done:
            self.reset_trace()
            self._agentic_reset_done = True

    def save_document_snippet(self, chunk):
        pass

    def save_library_snippet(self, chunk):
        pass

    def save_trace(self, payload):
        pass


class ChatRetrievalTarget(TransientRetrievalTarget):
    """Persists snippets and trace on a chat ``Message``."""

    def __init__(self, message: Any) -> None:
        super().__init__()
        self.message = message
        self._agentic_reset_done = False

    def reset_trace(self) -> None:
        """In-memory clear; ``save_trace`` persists whatever comes next."""
        self.message.retrieval_trace = None
        super().reset_trace()

    def begin_agentic_search(self) -> None:
        """Clear the previous generation's trace, once per turn."""
        if self._agentic_reset_done:
            return
        self._agentic_reset_done = True
        self.reset_trace()

    def save_document_snippet(self, chunk: Any) -> None:
        save_document_snippet(self.message, chunk)

    def save_library_snippet(self, chunk: Any) -> None:
        save_library_snippet(self.message, chunk)

    def save_trace(self, payload: Any) -> None:
        save_retrieval_trace(self.message, payload)


class WorkflowRetrievalTarget(TransientRetrievalTarget):
    """Persists snippets and trace on a ``WorkflowRunStep``."""

    def __init__(self, run_step: Any) -> None:
        super().__init__()
        self.run_step = run_step
        self._agentic_reset_done = False

    def reset_trace(self) -> None:
        self.run_step.retrieval_trace = None
        super().reset_trace()

    def begin_agentic_search(self) -> None:
        if self._agentic_reset_done:
            return
        self._agentic_reset_done = True
        self.reset_trace()

    def save_document_snippet(self, chunk: Any) -> None:
        """Best-effort, never raises — mirrors the chat snippet helpers."""
        try:
            file = File.active_objects.get(id=int(chunk.file_id))
            WorkflowStepSnippet.active_objects.create(
                workflow_run_step=self.run_step,
                file=file,
                library=None,
                text=chunk.text,
                similarity_score=(
                    chunk.rerank_score
                    if chunk.rerank_score is not None
                    else chunk.score
                ),
                chunk_index=chunk.chunk_index,
            )
        except Exception as exc:
            logger.warning("Failed to save workflow document snippet: %s", exc)

    def save_library_snippet(self, chunk: Any) -> None:
        try:
            WorkflowStepSnippet.active_objects.create(
                workflow_run_step=self.run_step,
                file=None,
                library=chunk.library,
                source_ref=chunk.source_ref,
                text=chunk.text,
                similarity_score=(
                    chunk.rerank_score
                    if chunk.rerank_score is not None
                    else chunk.score
                ),
                chunk_index=chunk.chunk_index,
            )
        except Exception as exc:
            logger.warning("Failed to save workflow library snippet: %s", exc)

    def save_trace(self, payload: Any) -> None:
        # save_retrieval_trace only touches ``retrieval_trace`` and
        # ``save(update_fields=...)`` — the same surface WorkflowRunStep has.
        save_retrieval_trace(self.run_step, payload)
