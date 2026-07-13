"""The RAG pipeline orchestrator.

Composes the stage classes into one flow:

    query analysis -> hybrid retrieve -> rerank -> conditional MMR
    -> grounding -> token-budgeted, [S#]-cited assembly

This is the single entry point callers use; it is deliberately thin — all heavy
lifting lives in the stage classes (rules.md §2/§8). Advanced mode always runs
query analysis, reranking, grounding, and trace capture; failures degrade safely.
"""

import logging
from dataclasses import replace
from typing import Callable, Optional

from core.helpers.openai import OpenAIWrapper
from core.services.rag.assembler import ContextAssembler
from core.services.rag.diversifier import MMRDiversifier
from core.services.rag.dtos import RetrievalRequest, RetrievalResult, RetrievedChunk
from core.services.rag.grounding import GroundingChecker
from core.services.rag.query_analyzer import QueryAnalyzer
from core.services.rag.reranker import Reranker
from core.services.rag.retriever import BaseRetriever, get_retriever
from core.services.rag.trace import build_trace

logger = logging.getLogger(__name__)

KeepHook = Optional[Callable[[int, RetrievedChunk], None]]


class RetrievalPipeline:
    """Orchestrates the retrieval stages. Inject stages for testing; defaults are sane."""

    def __init__(
        self,
        retriever: BaseRetriever,
        analyzer: Optional[QueryAnalyzer] = None,
        reranker: Optional[Reranker] = None,
        diversifier: Optional[MMRDiversifier] = None,
        grounding: Optional[GroundingChecker] = None,
        assembler: Optional[ContextAssembler] = None,
    ):
        self.retriever = retriever
        self.analyzer = analyzer or QueryAnalyzer()
        self.reranker = reranker or Reranker()
        self.diversifier = diversifier or MMRDiversifier()
        self.grounding = grounding or GroundingChecker()
        self.assembler = assembler or ContextAssembler()

    def run(
        self, request: RetrievalRequest, on_keep: KeepHook = None
    ) -> RetrievalResult:
        # 1) Understand the query (optional). Drives MMR gating + retrieval inputs.
        plan = self.analyzer.analyze(request.query)
        exploratory = bool(plan) and plan.is_exploratory

        # Retrieval inputs. The HyDE flag gates the *hypothesized* text (the
        # rewrite for BM25, the passage for the dense leg). Exact keywords are
        # extracted from the query itself — not hypothesized — so they always
        # boost the lexical leg: BM25 tokenizes them in, lifting documents that
        # contain the precise names / identifiers / places.
        dense_text, bm25_text = request.query, request.query
        if plan:
            if self.analyzer.use_hyde():
                dense_text = plan.hyde_passage or request.query
                bm25_text = plan.rewritten_query or request.query
            if plan.keywords:
                bm25_text = f"{bm25_text} {' '.join(plan.keywords)}".strip()

        query_vector = self.retriever.embed(dense_text)

        # 2) Retrieve a wider pool so rerank/MMR can trim it down.
        rerank_on = True
        multiplier = 5
        pool = self.retriever.search(
            replace(request, top_k=request.top_k * multiplier),
            query_vector,
            bm25_text,
            want_vectors=exploratory,  # MMR needs candidate embeddings
        )

        # 3) Rerank for true relevance (keep a wider set if MMR will trim further).
        reranked = pool
        if rerank_on:
            working_k = request.top_k * 2 if exploratory else request.top_k
            reranked = self.reranker.rerank(request.query, pool, working_k)
        rerank_applied = any(chunk.rerank_score is not None for chunk in reranked)

        # 4) Conditional MMR — diversity for exploratory queries only.
        if exploratory:
            final = self.diversifier.diversify(query_vector, reranked, request.top_k)
        else:
            final = reranked[: request.top_k]

        # 5) Grounding (trusted only when the reranker actually produced scores).
        grounding_threshold = self.reranker.grounding_threshold()
        grounding = (
            self.grounding.check(final, threshold=grounding_threshold)
            if rerank_applied
            else None
        )

        # 6) Assemble cited, budget-bounded context.
        blocks = self.assembler.assemble(final, grounding, on_keep=on_keep)

        # 7) Per-stage trace for the UI.
        trace = None
        if request.trace:
            trace = build_trace(
                query=request.query,
                plan=plan,
                pool=pool,
                reranked=reranked,
                rerank_applied=rerank_applied,
                mmr_applied=exploratory,
                mmr_reason=self._mmr_reason(plan, exploratory),
                grounding=grounding,
                grounding_threshold=grounding_threshold,
                final_size=len(final),
            )

        return RetrievalResult(
            chunks=final, blocks=blocks, grounding=grounding, plan=plan, trace=trace
        )

    @staticmethod
    def _mmr_reason(plan, exploratory: bool) -> str:
        if exploratory:
            return "applied — exploratory query"
        if plan is None:
            return "skipped — query analysis off"
        return f"skipped — {plan.intent} query"


def build_pipeline(
    source_type: str = "library", openai_client: Optional[OpenAIWrapper] = None
) -> RetrievalPipeline:
    """Factory: a ready pipeline for a given source type (library / document)."""
    return RetrievalPipeline(retriever=get_retriever(source_type, openai_client))
