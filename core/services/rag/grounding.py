"""Derive answer confidence from the best available retrieval score."""

from typing import List

from core.services.rag.dtos import Grounding, RetrievedChunk


class GroundingChecker:
    """Top-result confidence -> answer_found flag."""

    def check(self, chunks: List[RetrievedChunk], threshold: float = 0.3) -> Grounding:
        if not chunks:
            return Grounding(answer_found=False, top_score=0.0)
        top_score = max(
            chunk.rerank_score if chunk.rerank_score is not None else chunk.score
            for chunk in chunks
        )
        top_score = float(top_score or 0.0)
        return Grounding(answer_found=top_score >= threshold, top_score=top_score)
