"""Post-retrieval shaping for Track A: diversity (MMR) and answer-grounding.

  * ``mmr_diversify`` — Maximal Marginal Relevance. Trades a little relevance for
    novelty so the final set isn't near-duplicates. Helps *exploratory* queries;
    hurts *precise* lookups (it can diversify the answer away), so callers gate it
    on query intent.
  * ``answer_grounding`` — a confidence flag derived from the (calibrated) reranker
    score: did retrieval actually find something on-topic, or should the model say
    "not found"? Addresses the audit's mistakes #9/#10 cheaply.
"""

import math
from typing import Dict, List, Optional

from django.conf import settings


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def mmr_diversify(
    query_vector: List[float],
    candidates: List[Dict],
    top_k: int,
    lam: float = 0.7,
    vector_key: str = "vector",
) -> List[Dict]:
    """Greedily pick ``top_k`` candidates balancing relevance to the query (``lam``)
    against novelty vs. already-picked (``1 - lam``).

    Requires each candidate to carry its embedding under ``vector_key``; if vectors
    are missing it is a safe no-op (returns the first ``top_k`` unchanged).
    """
    usable = [c for c in candidates if c.get(vector_key)]
    if not usable or not query_vector:
        return candidates[:top_k]

    rel = {id(c): _cosine(query_vector, c[vector_key]) for c in usable}
    picked: List[Dict] = []
    pool = list(usable)
    while pool and len(picked) < top_k:
        best, best_score = None, -1e9
        for c in pool:
            novelty = (
                0.0
                if not picked
                else max(_cosine(c[vector_key], p[vector_key]) for p in picked)
            )
            score = lam * rel[id(c)] - (1 - lam) * novelty
            if score > best_score:
                best, best_score = c, score
        picked.append(best)
        pool.remove(best)
    return picked


def answer_grounding(
    results: List[Dict],
    score_key: str = "rerank_score",
    fallback_key: str = "score",
) -> Dict:
    """Return ``{answer_found, top_score}`` from the best candidate's score.

    Uses the calibrated reranker score when present (bge-reranker-v2-m3 emits 0-1),
    else the retrieval score. Threshold via ``RAG_GROUNDING_THRESHOLD`` (default 0.3,
    tuned for the calibrated reranker). Lets the caller tell the model to answer
    "not found in the sources" rather than hallucinate.
    """
    threshold = float(getattr(settings, "RAG_GROUNDING_THRESHOLD", 0.3))
    if not results:
        return {"answer_found": False, "top_score": 0.0}
    top = results[0]
    top_score = top.get(score_key)
    if top_score is None:
        top_score = top.get(fallback_key, 0.0)
    top_score = float(top_score or 0.0)
    return {"answer_found": top_score >= threshold, "top_score": top_score}
