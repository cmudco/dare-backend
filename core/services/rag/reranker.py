"""Rerank stage (audit mistake #7) — the highest precision-per-dollar lever.

A cross-encoder reads (query, chunk) together and scores true relevance, then we
keep the best few. Fully local (no API). Opt-in via ``RAG_RERANKER_ENABLED``; the
torch/sentence-transformers import is lazy so the backend never depends on them
unless reranking is turned on. Any failure is a safe no-op (original order kept).
"""

import logging
from dataclasses import replace
from typing import List

from core.services.rag.config import bool_flag, setting
from core.services.rag.dtos import RetrievedChunk

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

_model_cache = {}  # process-wide: loading the cross-encoder is expensive


class Reranker:
    """Re-sorts retrieved chunks by cross-encoder relevance."""

    def is_enabled(self) -> bool:
        return bool_flag("RAG_RERANKER_ENABLED")

    def rerank(
        self, query: str, chunks: List[RetrievedChunk], top_k: int
    ) -> List[RetrievedChunk]:
        if not chunks:
            return chunks
        if not self.is_enabled() or not query:
            return chunks[:top_k]
        try:
            model = self._get_model()
            pairs = [(query, (c.text or "")[:2000]) for c in chunks]
            scores = model.predict(pairs)
            ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
            return [
                replace(chunk, rerank_score=float(score))
                for chunk, score in ranked[:top_k]
            ]
        except Exception as exc:  # never let reranking break retrieval
            logger.warning("Rerank failed; using original order: %s", exc)
            return chunks[:top_k]

    def _get_model(self):
        name = setting("RAG_RERANKER_MODEL", DEFAULT_MODEL)
        if name not in _model_cache:
            # Lazy import: torch is only pulled in when reranking is enabled.
            from sentence_transformers import CrossEncoder

            device = setting("RAG_RERANKER_DEVICE", None)
            max_len = int(setting("RAG_RERANKER_MAX_LEN", 512))
            logger.info("Loading reranker %s (device=%s)", name, device or "auto")
            _model_cache[name] = CrossEncoder(name, device=device, max_length=max_len)
        return _model_cache[name]
