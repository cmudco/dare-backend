"""Advanced RAG retrieval pipeline (Track A).

Layered stages — each a powerful, self-contained class that owns its own model
or database access — composed by a thin orchestrator. Callers use
``build_pipeline`` and ``RetrievalRequest``; everything else is internal.

See ``docs/advanced-rag-implementation.md`` for the design and benchmarks.
"""

from core.services.rag.assembler import ContextAssembler
from core.services.rag.diversifier import MMRDiversifier
from core.services.rag.dtos import (
    Grounding,
    QueryPlan,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
    RetrievedChunk,
    TraceEntry,
)
from core.services.rag.grounding import GroundingChecker
from core.services.rag.pipeline import RetrievalPipeline, build_pipeline
from core.services.rag.query_analyzer import QueryAnalyzer
from core.services.rag.reranker import Reranker
from core.services.rag.retriever import BaseRetriever, LibraryRetriever, get_retriever

__all__ = [
    "build_pipeline",
    "RetrievalPipeline",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalTrace",
    "RetrievedChunk",
    "TraceEntry",
    "QueryPlan",
    "Grounding",
    "QueryAnalyzer",
    "BaseRetriever",
    "LibraryRetriever",
    "get_retriever",
    "Reranker",
    "MMRDiversifier",
    "GroundingChecker",
    "ContextAssembler",
]
