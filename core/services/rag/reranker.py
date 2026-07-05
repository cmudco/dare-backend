"""Rerank stage (audit mistake #7) — the highest precision-per-dollar lever.

A cross-encoder reads (query, chunk) together and scores true relevance, then we
keep the best few. Fully local (no API). The torch/sentence-transformers import
is lazy so the backend only loads it when advanced retrieval runs. Any failure is
a safe no-op (original order kept).
"""

import logging
from dataclasses import replace
from typing import List

from core.services.rag.config import setting
from core.services.rag.dtos import RetrievedChunk

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

_model_cache = {}  # process-wide: loading the cross-encoder is expensive


class Reranker:
    """Re-sorts retrieved chunks by cross-encoder relevance."""

    def model_name(self) -> str:
        return str(setting("RAG_RERANKER_MODEL", DEFAULT_MODEL))

    def grounding_threshold(self) -> float:
        """Default grounding cutoff for the configured reranker score scale.

        BGE rerankers are treated as calibrated 0-1 scores in this pipeline.
        MiniLM/MS MARCO cross-encoders emit logits, where 0 is a more sensible
        default relevance boundary. An explicit env/settings threshold wins.
        """
        explicit = setting("RAG_GROUNDING_THRESHOLD", None)
        if explicit is not None:
            return float(explicit)

        name = self.model_name().lower()
        if "minilm" in name or "ms-marco" in name:
            return 0.0
        return 0.3

    def rerank(
        self, query: str, chunks: List[RetrievedChunk], top_k: int
    ) -> List[RetrievedChunk]:
        if not chunks:
            return chunks
        if not query:
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
        name = self.model_name()
        if name not in _model_cache:
            # Lazy import: torch is only pulled in when reranking is enabled.
            from sentence_transformers import CrossEncoder

            device = setting("RAG_RERANKER_DEVICE", None)
            max_len = int(setting("RAG_RERANKER_MAX_LEN", 512))
            logger.info("Loading reranker %s (device=%s)", name, device or "auto")
            _model_cache[name] = CrossEncoder(name, device=device, max_length=max_len)
        return _model_cache[name]
