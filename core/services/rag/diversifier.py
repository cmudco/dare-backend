"""Diversity stage — Maximal Marginal Relevance (audit mistake #7).

Trades a little relevance for novelty so the final set isn't near-duplicates.
Helps *exploratory* queries; hurts *precise* lookups (it can diversify the answer
away), so the pipeline only invokes it when the QueryPlan intent is exploratory.
Needs candidate embeddings; a safe no-op if they're absent.
"""

import math
from typing import List

from core.services.rag.dtos import RetrievedChunk


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class MMRDiversifier:
    """Greedy MMR selection over chunks that carry their embedding."""

    def diversify(
        self,
        query_vector: List[float],
        chunks: List[RetrievedChunk],
        top_k: int,
        lam: float = 0.7,
    ) -> List[RetrievedChunk]:
        usable = [c for c in chunks if c.vector]
        if not usable or not query_vector:
            return chunks[:top_k]

        rel = {id(c): _cosine(query_vector, c.vector) for c in usable}
        picked: List[RetrievedChunk] = []
        pool = list(usable)
        while pool and len(picked) < top_k:
            best, best_score = None, -1e9
            for c in pool:
                novelty = (
                    0.0
                    if not picked
                    else max(_cosine(c.vector, p.vector) for p in picked)
                )
                score = lam * rel[id(c)] - (1 - lam) * novelty
                if score > best_score:
                    best, best_score = c, score
            picked.append(best)
            pool.remove(best)
        return picked
