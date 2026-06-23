"""Local cross-encoder reranker (Track A, final stage).

A cross-encoder reads (query, chunk) together and scores true relevance, then we
keep the best few. This is the highest precision-per-dollar RAG lever and runs
fully LOCAL — no API call. It re-sorts a larger candidate pool from hybrid
retrieval down to the snippets actually sent to the model.

Opt-in and lazy by design:
  * Disabled unless ``RAG_RERANKER_ENABLED`` is truthy (settings or env), so the
    backend never imports torch/sentence-transformers unless you turn it on.
  * The model is loaded once per process on first use and cached.

Settings (all optional):
  RAG_RERANKER_ENABLED  bool   default False
  RAG_RERANKER_MODEL    str    default "BAAI/bge-reranker-v2-m3"
  RAG_RERANKER_DEVICE   str    default None (auto: mps/cuda/cpu)
  RAG_RERANKER_MAX_LEN  int    default 512
"""

import logging
import os
from typing import Dict, List

from django.conf import settings

logger = logging.getLogger(__name__)

_MODEL = None  # cached CrossEncoder instance (process-wide)


def is_enabled() -> bool:
    """True if reranking is switched on via settings or environment."""
    if getattr(settings, "RAG_RERANKER_ENABLED", False):
        return True
    return os.environ.get("RAG_RERANKER_ENABLED", "").lower() in ("1", "true", "yes")


def _setting(name: str, default):
    return getattr(settings, name, None) or os.environ.get(name) or default


def _get_model():
    global _MODEL
    if _MODEL is None:
        # Lazy import: torch is only pulled in when reranking is actually enabled.
        from sentence_transformers import CrossEncoder

        model_name = _setting("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        device = _setting("RAG_RERANKER_DEVICE", None)
        max_len = int(_setting("RAG_RERANKER_MAX_LEN", 512))
        logger.info("Loading reranker %s (device=%s)", model_name, device or "auto")
        _MODEL = CrossEncoder(model_name, device=device, max_length=max_len)
    return _MODEL


def rerank(
    query: str,
    candidates: List[Dict],
    top_k: int,
    text_key: str = "text",
) -> List[Dict]:
    """Reorder ``candidates`` by cross-encoder relevance and return the best ``top_k``.

    Safe no-op fallbacks: if disabled, empty, or the model errors, returns the
    original order truncated to ``top_k`` — retrieval is never broken by rerank.
    Each returned item gets a ``rerank_score`` for transparency/thresholding.
    """
    if not candidates:
        return candidates
    if not is_enabled() or not query:
        return candidates[:top_k]
    try:
        model = _get_model()
        pairs = [(query, (c.get(text_key) or "")[:2000]) for c in candidates]
        scores = model.predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        out: List[Dict] = []
        for candidate, score in ranked[:top_k]:
            item = dict(candidate)
            item["rerank_score"] = float(score)
            out.append(item)
        return out
    except Exception as exc:  # never let reranking break retrieval
        logger.warning("Rerank failed; using original order: %s", exc)
        return candidates[:top_k]
