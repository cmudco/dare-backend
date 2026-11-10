from dataclasses import replace

from django.test import SimpleTestCase

from core.services.rag.dtos import (
    QueryPlan,
    ReferenceHop,
    RetrievalRequest,
    RetrievedChunk,
)
from core.services.rag.graph_walker import WalkResult
from core.services.rag.pipeline import GRAPH_EVIDENCE_SEATS, RetrievalPipeline


def chunk(file_id, score=0.5, **extra):
    return RetrievedChunk(
        text=f"text {file_id}",
        source_ref=f"file{file_id}",
        score=score,
        chunk_index=0,
        source_type="document",
        file_id=str(file_id),
        file_name=f"file{file_id}",
        **extra,
    )


def hop(file_id, graph_score):
    return chunk(
        file_id,
        via=ReferenceHop(0, "walk", "mappa", "MAPPA", "1"),
        graph_score=graph_score,
    )


class FakeAnalyzer:
    last_error = None

    def __init__(self, plan):
        self.plan = plan

    def analyze(self, query, payer_user_id, payer_bot_id, recent_turns=()):
        return self.plan

    def use_hyde(self):
        return False


class FakeRetriever:
    def __init__(self, pool):
        self.pool = pool

    def embed(self, text):
        return [0.1]

    def search(self, request, vector, bm25_text, want_vectors=False):
        return self.pool


class FakeReranker:
    def rerank(self, query, pool, k):
        scored = [replace(c, rerank_score=c.score) for c in pool]
        return sorted(scored, key=lambda c: -c.rerank_score)[:k]

    def grounding_threshold(self):
        return 0.3


class FakeWalker:
    def __init__(self, hops):
        self.hops = hops
        self.calls = []

    def walk(self, pool, user_id, file_ids, query_entities, connect=False):
        self.calls.append(tuple(query_entities))
        return WalkResult(pool=pool, hops=self.hops, nodes=5, edges=4)


class FakeExpander:
    def __init__(self):
        self.called = False

    def expand(self, pool, reranker_on, user_id, file_ids):
        self.called = True
        return []


def pipeline(plan, pool, walker, expander=None):
    return RetrievalPipeline(
        retriever=FakeRetriever(pool),
        analyzer=FakeAnalyzer(plan),
        reranker=FakeReranker(),
        walker=walker,
        expander=expander,
    )


REQUEST = RetrievalRequest(query="q", top_k=2, file_ids=(1, 2), user_id=1, trace=True)


class RouterTests(SimpleTestCase):
    def test_graph_route_runs_the_walk_with_named_entities(self):
        walker = FakeWalker([])
        expander = FakeExpander()
        plan = QueryPlan(route="graph", query_entities=("MAPPA",))
        pipeline(plan, [chunk(1)], walker, expander).run(REQUEST)
        self.assertEqual(walker.calls, [("MAPPA",)])
        self.assertFalse(expander.called)

    def test_direct_route_keeps_the_one_hop_expand(self):
        walker = FakeWalker([])
        expander = FakeExpander()
        pipeline(QueryPlan(route="direct"), [chunk(1)], walker, expander).run(REQUEST)
        self.assertEqual(walker.calls, [])
        self.assertTrue(expander.called)

    def test_failed_analysis_never_runs_the_walk(self):
        walker = FakeWalker([])
        pipeline(None, [chunk(1)], walker).run(REQUEST)
        self.assertEqual(walker.calls, [])

    def test_no_selected_files_never_runs_the_walk(self):
        walker = FakeWalker([])
        plan = QueryPlan(route="graph")
        pipeline(plan, [chunk(1)], walker).run(replace(REQUEST, file_ids=()))
        self.assertEqual(walker.calls, [])


class GraphEvidenceTests(SimpleTestCase):
    def run_graph(self, pool, hops):
        plan = QueryPlan(route="graph", query_entities=("MAPPA",))
        return pipeline(plan, pool, FakeWalker(hops)).run(REQUEST)

    def test_linked_chunks_add_seats_without_removing_direct_evidence(self):
        result = self.run_graph(
            [chunk(1, 0.9), chunk(2, 0.8), chunk(3, 0.7)], [hop(9, 0.5)]
        )
        files = [c.file_id for c in result.chunks]
        self.assertEqual(files, ["1", "2", "9"])

    def test_seats_are_capped(self):
        hops = [hop(10 + n, 0.5 - n / 100) for n in range(GRAPH_EVIDENCE_SEATS + 3)]
        result = self.run_graph([chunk(1, 0.9)], hops)
        self.assertEqual(len(result.chunks), 1 + GRAPH_EVIDENCE_SEATS)

    def test_trace_carries_route_walk_and_linked_chunks(self):
        result = self.run_graph([chunk(1, 0.9)], [hop(9, 0.5)])
        payload = result.trace.to_payload()
        self.assertEqual(payload["queryAnalysis"]["route"], "graph")
        self.assertEqual(payload["expand"]["walk"]["nodes"], 5)
        self.assertEqual(len(payload["expand"]["added"]), 1)
