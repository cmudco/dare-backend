"""Builds a RetrievalTrace from the pipeline's per-stage outputs.

Kept out of the pipeline so the orchestrator stays thin (rules.md §2/§8): the
pipeline just hands over the stage lists, this turns them into the UI payload —
including each reranked chunk's rank movement vs. the hybrid stage.
"""

from typing import List, Optional, Tuple

from core.services.rag.dtos import (
    Grounding,
    QueryPlan,
    RetrievalTrace,
    RetrievedChunk,
    TraceEntry,
)

HYBRID_PREVIEW_LIMIT = 8  # cap the candidate pool shown in the trace
PREVIEW_CHARS = 90


def _key(chunk: RetrievedChunk) -> Tuple[str, int]:
    return (chunk.source_ref, chunk.chunk_index)


def _preview(text: str) -> str:
    return " ".join((text or "").split())[:PREVIEW_CHARS]


def _entries(chunks: List[RetrievedChunk], prev_ranks=None, use_rerank=False):
    entries = []
    for rank, chunk in enumerate(chunks, 1):
        score = (
            chunk.rerank_score
            if use_rerank and chunk.rerank_score is not None
            else chunk.score
        )
        entries.append(
            TraceEntry(
                source_ref=chunk.source_ref,
                chunk_index=chunk.chunk_index,
                score=float(score or 0.0),
                rank=rank,
                prev_rank=prev_ranks.get(_key(chunk)) if prev_ranks else None,
                preview=_preview(chunk.text),
            )
        )
    return entries


def build_trace(
    *,
    query: str,
    plan: Optional[QueryPlan],
    pool: List[RetrievedChunk],
    reranked: List[RetrievedChunk],
    rerank_applied: bool,
    mmr_applied: bool,
    mmr_reason: str,
    grounding: Optional[Grounding],
    grounding_threshold: float,
    final_size: int,
) -> RetrievalTrace:
    pool_ranks = {_key(c): i for i, c in enumerate(pool, 1)}
    return RetrievalTrace(
        query=query,
        plan=plan,
        pool_size=len(pool),
        hybrid=_entries(pool[:HYBRID_PREVIEW_LIMIT]),
        reranked=_entries(reranked, prev_ranks=pool_ranks, use_rerank=rerank_applied),
        rerank_applied=rerank_applied,
        mmr_applied=mmr_applied,
        mmr_reason=mmr_reason,
        grounding=grounding,
        grounding_threshold=grounding_threshold,
        final_size=final_size,
    )
