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
from typing import Callable, Dict, List, Optional, Tuple

from core.helpers.openai import OpenAIWrapper
from core.services.rag.assembler import ContextAssembler
from core.services.rag.config import flag
from core.services.rag.diversifier import MMRDiversifier
from core.services.rag.dtos import RetrievalRequest, RetrievalResult, RetrievedChunk
from core.services.rag.expander import GraphExpander
from core.services.rag.grounding import GroundingChecker
from core.services.rag.query_analyzer import QueryAnalyzer
from core.services.rag.reference_extractor import extract_pointers
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
        expander: Optional[GraphExpander] = None,
    ):
        self.retriever = retriever
        self.analyzer = analyzer or QueryAnalyzer()
        self.reranker = reranker or Reranker()
        self.diversifier = diversifier or MMRDiversifier()
        self.grounding = grounding or GroundingChecker()
        self.assembler = assembler or ContextAssembler()
        self.expander = expander

    def run(
        self, request: RetrievalRequest, on_keep: KeepHook = None
    ) -> RetrievalResult:
        # 1) Understand the query (optional). Drives MMR gating + retrieval inputs.
        plan = self.analyzer.analyze(
            request.query,
            request.payer_user_id,
            request.payer_bot_id,
        )
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

        # 2b) Follow the document graph one hop — stored pointers, then
        # entities shared with another selected file (document path only).
        expanded: List[RetrievedChunk] = []
        expand_applied = False
        if self.expander is not None and request.file_ids:
            expanded = self.expander.expand(
                pool,
                reranker_on=rerank_on,
                user_id=request.user_id,
                file_ids=request.file_ids,
            )
            expand_applied = True
        candidates = [*pool, *expanded]

        # 3) Rerank for true relevance (keep a wider set if MMR will trim further).
        # The cross-encoder scores every direct hit and graph hop before the
        # evidence guard below decides which items may spend a result slot.
        reranked = candidates
        if rerank_on:
            working_k = request.top_k * 2 if exploratory else request.top_k
            reranked = self.reranker.rerank(request.query, candidates, len(candidates))
        rerank_applied = any(chunk.rerank_score is not None for chunk in reranked)

        # 4) Conditional MMR — diversity for exploratory queries only. Pick
        # direct evidence first; a graph hop may only spend a result slot when
        # its own source survived. This prevents incidental pointers in a
        # discarded candidate from evicting useful evidence.
        ranked_candidates = reranked
        reranked = ranked_candidates[:working_k]
        direct = [chunk for chunk in ranked_candidates if chunk.via is None]
        hops = [chunk for chunk in ranked_candidates if chunk.via is not None]
        mmr_applied = (
            exploratory
            and bool(query_vector)
            and bool(direct)
            and all(c.vector for c in direct[:working_k])
        )
        if mmr_applied:
            picked = self.diversifier.diversify(
                query_vector, direct[:working_k], request.top_k
            )
        else:
            picked = direct[: request.top_k]
        final = self._merge_hops(
            request.query,
            picked,
            hops,
            request.top_k,
            rerank_applied=rerank_applied,
        )

        # 5) Assemble cited, budget-bounded context.
        kept = []
        citation_offset = (
            request.citations.count if request.citations is not None else 0
        )

        def record_kept(position, chunk):
            kept.append(chunk)
            if on_keep is not None:
                on_keep(position, chunk)

        blocks = self.assembler.assemble(
            final, on_keep=record_kept, citations=request.citations
        )
        final = kept

        # 6) Confidence describes only the evidence that fits in the context.
        grounding_threshold = self.reranker.grounding_threshold()
        grounding = (
            self.grounding.check(final, threshold=grounding_threshold)
            if rerank_applied
            else None
        )
        blocks = ContextAssembler.grounding_blocks(grounding) + blocks

        # 7) Per-stage trace for the UI.
        trace = None
        if request.trace:
            trace = build_trace(
                query=request.query,
                plan=plan,
                pool=pool,
                reranked=reranked,
                rerank_applied=rerank_applied,
                mmr_applied=mmr_applied,
                mmr_reason=(
                    self._mmr_reason(plan, mmr_applied, self.analyzer.last_error)
                    if not exploratory or mmr_applied
                    else "skipped — candidate vectors unavailable"
                ),
                analysis_error=self.analyzer.last_error,
                grounding=grounding,
                grounding_threshold=grounding_threshold,
                final_size=len(final),
                expanded=expanded,
                expand_applied=expand_applied,
                final=final,
                citation_offset=citation_offset,
            )

        return RetrievalResult(
            chunks=final, blocks=blocks, grounding=grounding, plan=plan, trace=trace
        )

    @staticmethod
    def _mmr_reason(plan, exploratory: bool, analysis_error: Optional[str]) -> str:
        if exploratory:
            return "applied — exploratory query"
        if plan is None:
            # "off" would read as a deliberate setting; there is no such
            # switch, so a missing plan means the stage did not survive.
            return (
                "skipped — query analysis unavailable"
                if analysis_error
                else "skipped — no query analysis"
            )
        return f"skipped — {plan.intent} query"

    @staticmethod
    def _anchor_hops(chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        """Place each followed pointer directly after the chunk that pointed to it.

        A resolved "see section 2.1" is the author's own link, so the target
        keeps the seat right below its source regardless of how the reranker
        scored its text. Scores are left untouched; only the order changes.

        A hop that outscored its own source, or whose source the reranker
        dropped, is left exactly where its score put it — there is nothing
        above it to sit under. A source that is itself a hop still anchors
        its own followers, so a chain is re-seated whole.
        """
        position = {(c.file_id, c.chunk_index): i for i, c in enumerate(chunks)}
        following: Dict[Tuple[str, int], List[int]] = {}
        moved = set()
        for index, chunk in enumerate(chunks):
            if chunk.via is None:
                continue
            source_key = (
                chunk.via.source_file_id or chunk.file_id,
                chunk.via.source_chunk_index,
            )
            if position.get(source_key, index) < index:
                following.setdefault(source_key, []).append(index)
                moved.add(index)
        if not moved:
            return chunks
        ordered: List[RetrievedChunk] = []

        def emit(index: int) -> None:
            chunk = chunks[index]
            ordered.append(chunk)
            for hop in following.get((chunk.file_id, chunk.chunk_index), ()):
                emit(hop)

        for index in range(len(chunks)):
            if index not in moved:
                emit(index)
        return ordered

    @classmethod
    def _merge_hops(
        cls,
        query: str,
        direct: List[RetrievedChunk],
        hops: List[RetrievedChunk],
        top_k: int,
        *,
        rerank_applied: bool,
    ) -> List[RetrievedChunk]:
        """Add useful graph context without sacrificing stronger evidence.

        A hop is eligible only when the chunk containing its pointer was
        selected. Spare result slots may be filled freely. Once full, a hop
        replaces the weakest other direct hit only when the query names that
        reference (for example ``Chapter 10``) or the reranker scored the hop
        above that hit. A lower-scored link sits immediately below its source;
        a link that genuinely outscored the source keeps the higher seat.
        """
        selected = list(direct[:top_k])
        if not selected or top_k <= 0:
            return selected

        for hop in hops:
            if hop.via is None:
                continue
            source_key = (
                hop.via.source_file_id or hop.file_id,
                hop.via.source_chunk_index,
            )
            if not any(
                (chunk.file_id, chunk.chunk_index) == source_key for chunk in selected
            ):
                continue
            target_key = (hop.file_id, hop.chunk_index)
            if any(
                (chunk.file_id, chunk.chunk_index) == target_key for chunk in selected
            ):
                continue

            if len(selected) >= top_k:
                victims = [
                    chunk
                    for chunk in selected
                    if chunk.via is None
                    and (chunk.file_id, chunk.chunk_index) != source_key
                ]
                if not victims:
                    continue
                victim = min(victims, key=cls._ranking_score)
                named_hop = cls._query_names_hop(query, hop)
                if not named_hop and (
                    not rerank_applied
                    or cls._ranking_score(hop) <= cls._ranking_score(victim)
                ):
                    continue
                selected.remove(victim)

            source_position = next(
                index
                for index, chunk in enumerate(selected)
                if (chunk.file_id, chunk.chunk_index) == source_key
            )
            source = selected[source_position]
            insert_at = source_position + 1
            if rerank_applied and cls._ranking_score(hop) > cls._ranking_score(source):
                insert_at = source_position
            while (
                insert_at < len(selected)
                and selected[insert_at].via is not None
                and (
                    selected[insert_at].via.source_file_id
                    or selected[insert_at].file_id
                )
                == source_key[0]
                and selected[insert_at].via.source_chunk_index == source_key[1]
            ):
                insert_at += 1
            selected.insert(insert_at, hop)

        return selected[:top_k]

    @staticmethod
    def _ranking_score(chunk: RetrievedChunk) -> float:
        return chunk.rerank_score if chunk.rerank_score is not None else chunk.score

    @staticmethod
    def _query_names_hop(query: str, hop: RetrievedChunk) -> bool:
        if hop.via is None:
            return False
        return any(
            pointer.kind == hop.via.kind and pointer.key == hop.via.key
            for pointer in extract_pointers(query)
        )


def _load_hops(keys, user_id):
    # Local import: the map service pulls in Django models at import time.
    from files.services.document_map_service import DocumentMapService

    return DocumentMapService.load_hops(keys, user_id)


def _load_entity_hops(keys, user_id, file_ids):
    # Local import: the map service pulls in Django models at import time.
    from files.services.document_map_service import DocumentMapService

    return DocumentMapService.load_entity_hops(keys, user_id, file_ids)


def build_pipeline(
    source_type: str = "library", openai_client: Optional[OpenAIWrapper] = None
) -> RetrievalPipeline:
    """Factory: a ready pipeline for a given source type (library / document)."""
    expander = None
    if source_type == "document" and flag("RAG_GRAPH_EXPAND_ENABLED", True):
        expander = GraphExpander(
            loader=_load_hops,
            entity_loader=(
                _load_entity_hops if flag("RAG_ENTITY_HOPS_ENABLED", True) else None
            ),
        )
    return RetrievalPipeline(
        retriever=get_retriever(source_type, openai_client), expander=expander
    )
