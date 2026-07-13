"""Typed data objects that flow through the RAG pipeline.

Frozen dataclasses (rules.md §1) so stages can't mutate each other's data — a
stage that enriches a chunk (e.g. the reranker adding a score) returns a NEW
instance via ``dataclasses.replace``. This replaces the old ``List[Dict[str, Any]]``
candidate-passing (rules.md §8 "Dict[str, Any] everywhere").
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class QueryPlan:
    """Structured understanding of a raw query (output of QueryAnalyzer)."""

    intent: str = "precise_lookup"  # precise_lookup | exploratory | comparison
    keywords: Tuple[str, ...] = ()
    rewritten_query: str = ""
    hyde_passage: str = ""

    @property
    def is_exploratory(self) -> bool:
        """Exploratory queries benefit from MMR diversity; precise ones don't."""
        return self.intent == "exploratory"


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved passage and everything the pipeline learns about it."""

    text: str
    source_ref: str
    score: float
    chunk_index: int = 0
    source_type: str = "library"  # library | document
    file_id: str = ""
    file_name: str = ""
    library: Optional[Any] = None  # SharedLibrary (Any avoids a circular import)
    vector: Optional[List[float]] = None  # populated only when MMR needs it
    rerank_score: Optional[float] = None  # set by the reranker


@dataclass(frozen=True)
class RetrievalRequest:
    """Everything the pipeline needs for one retrieval."""

    query: str
    top_k: int = 6
    library_ids: Tuple[int, ...] = ()
    file_ids: Tuple[int, ...] = ()
    user_id: Optional[int] = None
    similarity_threshold: float = 0.0
    trace: bool = False  # capture a per-stage RetrievalTrace for the UI


@dataclass(frozen=True)
class Grounding:
    """Confidence signal: did retrieval actually find something on-topic?"""

    answer_found: bool
    top_score: float


# ---------------------------------------------------------------------------
# Trace — a per-stage record of how an answer was retrieved, for the UI.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceEntry:
    """One chunk as it appeared at one pipeline stage."""

    source_ref: str
    chunk_index: int
    score: float
    rank: int
    prev_rank: Optional[int] = None  # rank in the previous stage (rank movement)
    preview: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            "sourceRef": self.source_ref,
            "chunkIndex": self.chunk_index,
            "score": round(self.score, 4),
            "rank": self.rank,
            "prevRank": self.prev_rank,
            "preview": self.preview,
        }


@dataclass(frozen=True)
class RetrievalTrace:
    """How an answer was retrieved, stage by stage — for the frontend trace view."""

    query: str
    plan: Optional[QueryPlan]
    pool_size: int
    hybrid: List[TraceEntry]  # top fused candidates
    reranked: List[TraceEntry]  # after rerank (carries prev_rank from hybrid)
    rerank_applied: bool
    mmr_applied: bool
    mmr_reason: str
    grounding: Optional[Grounding]
    grounding_threshold: float
    final_size: int

    def to_payload(self) -> Dict[str, Any]:
        """Camelized payload for the frontend (rules.md §11: typed, no FE parsing)."""
        return {
            "query": self.query,
            "queryAnalysis": (
                {
                    "intent": self.plan.intent,
                    "keywords": list(self.plan.keywords),
                    "rewrittenQuery": self.plan.rewritten_query,
                    "hydePassage": self.plan.hyde_passage,
                }
                if self.plan
                else None
            ),
            "hybrid": {
                "poolSize": self.pool_size,
                "topCandidates": [e.to_payload() for e in self.hybrid],
            },
            "rerank": {
                "applied": self.rerank_applied,
                "results": [e.to_payload() for e in self.reranked],
            },
            "mmr": {"applied": self.mmr_applied, "reason": self.mmr_reason},
            "grounding": (
                {
                    "answerFound": self.grounding.answer_found,
                    "topScore": round(self.grounding.top_score, 4),
                    "threshold": self.grounding_threshold,
                }
                if self.grounding
                else None
            ),
            "finalSize": self.final_size,
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Final output of the pipeline."""

    chunks: List[RetrievedChunk] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)  # formatted, [S#]-cited context
    grounding: Optional[Grounding] = None
    plan: Optional[QueryPlan] = None
    trace: Optional[RetrievalTrace] = None
