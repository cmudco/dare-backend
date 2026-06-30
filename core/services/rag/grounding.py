"""Answer-grounding stage (audit mistakes #9/#10).

Derives a confidence flag from the calibrated reranker score: did retrieval find
something genuinely on-topic, or should the model say "not in the sources"? Cheap
to compute and only trusted when the reranker actually ran (its score is
calibrated 0-1, unlike the raw RRF retrieval score).
"""

from typing import List

from core.services.rag.config import setting
from core.services.rag.dtos import Grounding, RetrievedChunk


class GroundingChecker:
    """Top-result confidence -> answer_found flag."""

    def check(self, chunks: List[RetrievedChunk]) -> Grounding:
        threshold = float(setting("RAG_GROUNDING_THRESHOLD", 0.3))
        if not chunks:
            return Grounding(answer_found=False, top_score=0.0)
        top = chunks[0]
        top_score = top.rerank_score if top.rerank_score is not None else top.score
        top_score = float(top_score or 0.0)
        return Grounding(answer_found=top_score >= threshold, top_score=top_score)
