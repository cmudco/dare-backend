from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.services.rag.dtos import (
    QueryPlan,
    ReferenceHop,
    RetrievalRequest,
    RetrievedChunk,
)
from core.services.rag.expander import GraphExpander
from core.services.rag.pipeline import RetrievalPipeline, build_pipeline


class FakeAnalyzer:
    last_error = None

    def analyze(self, query, payer_user_id, payer_bot_id):
        return None

    def use_hyde(self):
        return False


class ExploratoryAnalyzer:
    last_error = None

    def analyze(self, query, payer_user_id, payer_bot_id):
        return QueryPlan(intent="exploratory")

    def use_hyde(self):
        return False


class FakeReranker:
    def rerank(self, query, pool, k):
        return pool[:k]

    def grounding_threshold(self):
        return 0.3


class FakeDiversifier:
    """Mimics MMR: only picks among chunks that carry a vector."""

    def diversify(self, query_vector, chunks, k):
        return [c for c in chunks if c.vector][:k]


class ScoringReranker:
    """Assigns caller-supplied scores to candidates, in the order the pipeline
    hands them over, then sorts descending and truncates to k — like a real
    cross-encoder rerank would."""

    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, pool, k):
        scored = [replace(c, rerank_score=s) for c, s in zip(pool, self.scores)]
        ranked = sorted(scored, key=lambda c: c.rerank_score, reverse=True)
        return ranked[:k]

    def grounding_threshold(self):
        return 0.3


class DroppingReranker:
    """Scores candidates by chunk index and drops the ones it has no score for,
    like a cross-encoder whose batch lost an entry."""

    def __init__(self, scores_by_index):
        self.scores_by_index = scores_by_index

    def rerank(self, query, pool, k):
        scored = [
            replace(chunk, rerank_score=self.scores_by_index[chunk.chunk_index])
            for chunk in pool
            if chunk.chunk_index in self.scores_by_index
        ]
        return sorted(scored, key=lambda c: c.rerank_score, reverse=True)[:k]

    def grounding_threshold(self):
        return 0.3


class FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks

    def embed(self, text):
        return [0.0]

    def search(self, request, query_vector, query_text, want_vectors):
        return list(self.chunks)


def hit(index):
    return RetrievedChunk(
        text=f"chunk {index} see section 7.2",
        source_ref="book.pdf",
        score=0.9,
        chunk_index=index,
        source_type="document",
        file_id="7",
        file_name="book.pdf",
    )


def hit_with_vector(index, vector=(0.1,)):
    return RetrievedChunk(
        text=f"chunk {index} see section 7.2",
        source_ref="book.pdf",
        score=0.9,
        chunk_index=index,
        source_type="document",
        file_id="7",
        file_name="book.pdf",
        vector=list(vector),
    )


def spec_hit(index, text):
    return RetrievedChunk(
        text=text,
        source_ref="spec.pdf",
        score=0.5,
        chunk_index=index,
        source_type="document",
        file_id="7",
        file_name="spec.pdf",
    )


def section_hop_loader(source_index=27, target_index=1):
    return lambda keys, user_id: {
        ("7", source_index): [
            SimpleNamespace(
                kind="section",
                key="2.1",
                raw_text="section 2.1",
                chunk_index=target_index,
                text="Bracket A: 0.35 mm. Bracket B: 0.80 mm.",
                page_start=None,
                page_end=None,
                section="2.1",
                file_name="spec.pdf",
            )
        ]
    }


def one_hop_loader(source_index, target_index=31):
    return lambda keys, user_id: {
        ("7", source_index): [
            SimpleNamespace(
                kind="section",
                key="7.2",
                raw_text="see section 7.2",
                chunk_index=target_index,
                text="A tombstone…",
                page_start=204,
                page_end=204,
                section="7.2 Collisions",
                file_name="book.pdf",
            )
        ]
    }


class PipelineExpandTests(SimpleTestCase):
    def test_expanded_chunk_reaches_context_and_trace(self):
        loader = lambda keys, user_id: {
            ("7", 38): [
                SimpleNamespace(
                    kind="section",
                    key="7.2",
                    raw_text="see section 7.2",
                    chunk_index=31,
                    text="A tombstone…",
                    page_start=204,
                    page_end=204,
                    section="7.2 Collisions",
                    file_name="book.pdf",
                )
            ]
        }
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([hit(38)]),
            analyzer=FakeAnalyzer(),
            reranker=FakeReranker(),
            expander=GraphExpander(loader),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="how is deletion handled?",
                top_k=4,
                file_ids=(7,),
                user_id=1,
                trace=True,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [38, 31])
        self.assertIn('followed "see section 7.2" from [S1]', result.blocks[1])
        self.assertTrue(result.trace.expand_applied)
        self.assertEqual(result.trace.expanded[0].chunk_index, 31)

    def test_no_file_ids_skips_expansion(self):
        calls = []
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([hit(38)]),
            analyzer=FakeAnalyzer(),
            reranker=FakeReranker(),
            expander=GraphExpander(lambda keys, user_id: calls.append(keys) or {}),
        )
        result = pipeline.run(
            RetrievalRequest(query="q", top_k=4, library_ids=(1,), trace=True)
        )
        self.assertEqual(calls, [])
        self.assertFalse(result.trace.expand_applied)

    def test_exploratory_query_keeps_hops_past_mmr(self):
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([hit_with_vector(38)]),
            analyzer=ExploratoryAnalyzer(),
            reranker=FakeReranker(),
            diversifier=FakeDiversifier(),
            expander=GraphExpander(one_hop_loader(38)),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="how is deletion handled?",
                top_k=4,
                file_ids=(7,),
                user_id=1,
                trace=True,
            )
        )

        self.assertIn(31, [c.chunk_index for c in result.chunks])
        self.assertTrue(result.trace.mmr_applied)

    def test_exploratory_query_reserves_room_for_named_hops(self):
        direct_hits = [
            hit_with_vector(10),
            hit_with_vector(20),
            hit_with_vector(30),
        ]
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever(direct_hits),
            analyzer=ExploratoryAnalyzer(),
            reranker=FakeReranker(),
            diversifier=FakeDiversifier(),
            expander=GraphExpander(one_hop_loader(10)),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="What does section 7.2 say about deletion?",
                top_k=2,
                file_ids=(7,),
                user_id=1,
                trace=True,
            )
        )

        self.assertEqual(len(result.chunks), 2)
        self.assertIn(31, [c.chunk_index for c in result.chunks])

    def test_build_pipeline_wires_expander_for_documents_only(self):
        with patch("core.services.rag.pipeline.get_retriever"):
            self.assertIsNotNone(build_pipeline("document").expander)
            self.assertIsNone(build_pipeline("library").expander)
            with override_settings(RAG_GRAPH_EXPAND_ENABLED="false"):
                self.assertIsNone(build_pipeline("document").expander)

    def test_followed_hop_ranks_just_below_its_source(self):
        source_hit = spec_hit(
            27,
            "Alignment limits for the brackets are governed by the bracket "
            "table; see section 2.1 for the values.",
        )
        decoy_a = spec_hit(40, "This paragraph discusses alignment limits.")
        decoy_b = spec_hit(41, "Another paragraph about alignment limits.")
        decoy_c = spec_hit(42, "A third paragraph mentioning alignment limits.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, decoy_a, decoy_b, decoy_c]),
            analyzer=FakeAnalyzer(),
            reranker=ScoringReranker([0.95, 0.90, 0.88, 0.80, 0.05]),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what alignment limits are listed in section 2.1?",
                top_k=3,
                file_ids=(7,),
                user_id=1,
                trace=True,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 1, 40])
        # The seat is the author's link; the score stays the reranker's own.
        self.assertEqual(result.chunks[1].rerank_score, 0.05)
        self.assertEqual(result.chunks[0].rerank_score, 0.95)
        self.assertIn('followed "section 2.1" from [S1]', result.blocks[1])

    def test_hop_that_outscores_its_source_keeps_its_place(self):
        source_hit = spec_hit(
            27,
            "Alignment limits for the brackets are governed by the bracket "
            "table; see section 2.1 for the values.",
        )
        decoy_a = spec_hit(40, "This paragraph discusses alignment limits.")
        decoy_b = spec_hit(41, "Another paragraph about alignment limits.")
        decoy_c = spec_hit(42, "A third paragraph mentioning alignment limits.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, decoy_a, decoy_b, decoy_c]),
            analyzer=FakeAnalyzer(),
            reranker=ScoringReranker([0.95, 0.90, 0.88, 0.80, 0.97]),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what alignment limits are listed in section 2.1?",
                top_k=3,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [1, 27, 40])
        self.assertEqual(result.chunks[0].rerank_score, 0.97)

    def test_anchoring_holds_on_logit_scale_scores(self):
        """A reranker that emits logits, where a floor below a negative source
        score would sit *above* it and lift the hop over the chunk it came from."""
        source_hit = spec_hit(
            27,
            "Alignment limits for the brackets are governed by the bracket "
            "table; see section 2.1 for the values.",
        )
        decoy_a = spec_hit(40, "This paragraph discusses alignment limits.")
        decoy_b = spec_hit(41, "Another paragraph about alignment limits.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, decoy_a, decoy_b]),
            analyzer=FakeAnalyzer(),
            reranker=ScoringReranker([-2.0, -2.4, -2.6, -9.0]),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what alignment limits are listed in section 2.1?",
                top_k=2,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 1])
        self.assertEqual(
            [c.rerank_score for c in result.chunks],
            [-2.0, -9.0],
        )

    def test_named_low_scored_hop_survives_reranker_truncation(self):
        source_hit = spec_hit(
            27,
            "Alignment limits for the brackets are governed by the bracket "
            "table; see section 2.1 for the values.",
        )
        decoy_a = spec_hit(40, "This paragraph discusses alignment limits.")
        decoy_b = spec_hit(41, "Another paragraph about alignment limits.")
        decoy_c = spec_hit(42, "A third paragraph mentioning alignment limits.")
        decoy_d = spec_hit(43, "A fourth paragraph mentioning alignment limits.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, decoy_a, decoy_b, decoy_c, decoy_d]),
            analyzer=FakeAnalyzer(),
            reranker=ScoringReranker([0.95, 0.90, 0.88, 0.80, 0.70, 0.05]),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what alignment limits are listed in section 2.1?",
                top_k=2,
                file_ids=(7,),
                user_id=1,
                trace=True,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 1])
        self.assertEqual(result.chunks[1].rerank_score, 0.05)
        self.assertIn('followed "section 2.1" from [S1]', result.blocks[1])

    def test_hop_without_its_source_is_not_returned(self):
        """A followed target is only useful when its selected source explains why."""
        source_hit = spec_hit(
            27,
            "Alignment limits for the brackets are governed by the bracket "
            "table; see section 2.1 for the values.",
        )
        decoy_a = spec_hit(40, "This paragraph discusses alignment limits.")
        decoy_b = spec_hit(41, "Another paragraph about alignment limits.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, decoy_a, decoy_b]),
            analyzer=FakeAnalyzer(),
            reranker=DroppingReranker({40: 0.90, 1: 0.70, 41: 0.50}),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what are the alignment limits?",
                top_k=3,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [40, 41])

    def test_incidental_low_scored_pointer_does_not_evict_direct_evidence(self):
        source_hit = spec_hit(
            27,
            "Properties use attributes and mention Chapter 2 as background reading.",
        )
        direct_answer = spec_hit(40, "Use a property setter with @name.setter.")
        other_answer = spec_hit(41, "Begin with a normal attribute until needed.")
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, direct_answer, other_answer]),
            analyzer=FakeAnalyzer(),
            reranker=ScoringReranker([0.95, 0.90, 0.80, 0.01]),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="how should properties be implemented?",
                top_k=3,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 40, 41])

    def test_unscored_hop_does_not_evict_direct_evidence(self):
        source_hit = replace(
            spec_hit(
                27,
                "Properties mention section 2.1 as background reading.",
            ),
            score=0.90,
        )
        direct_answer = replace(
            spec_hit(40, "Use a property setter with @name.setter."), score=0.80
        )
        other_answer = replace(
            spec_hit(41, "Begin with a normal attribute until needed."), score=0.70
        )
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, direct_answer, other_answer]),
            analyzer=FakeAnalyzer(),
            reranker=FakeReranker(),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="how should properties be implemented?",
                top_k=3,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 40, 41])

    def test_named_unscored_hop_may_replace_direct_evidence(self):
        source_hit = replace(
            spec_hit(27, "Properties mention section 2.1 as background reading."),
            score=0.90,
        )
        direct_answer = replace(spec_hit(40, "Direct evidence."), score=0.80)
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([source_hit, direct_answer]),
            analyzer=FakeAnalyzer(),
            reranker=FakeReranker(),
            expander=GraphExpander(section_hop_loader()),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="what does section 2.1 say?",
                top_k=2,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual([c.chunk_index for c in result.chunks], [27, 1])

    def test_exploratory_top_k_one_keeps_the_direct_hit(self):
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([hit_with_vector(38)]),
            analyzer=ExploratoryAnalyzer(),
            reranker=FakeReranker(),
            diversifier=FakeDiversifier(),
            expander=GraphExpander(one_hop_loader(38)),
        )

        result = pipeline.run(
            RetrievalRequest(
                query="how is deletion handled?",
                top_k=1,
                file_ids=(7,),
                user_id=1,
            )
        )

        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(result.chunks[0].chunk_index, 38)

    def test_entity_hop_is_cited_as_shared(self):
        entity = SimpleNamespace(
            kind="entity",
            key="wilkins abbs",
            raw_text="Wilkins Abbs",
            chunk_index=4,
            text="Affidavit of Wilkins Abbs…",
            page_start=2,
            page_end=2,
            section="Affidavit",
            file_name="affidavit.pdf",
            entity_kind="person",
            file_id="8",
        )
        pipeline = RetrievalPipeline(
            retriever=FakeRetriever([hit(38)]),
            analyzer=FakeAnalyzer(),
            reranker=FakeReranker(),
            expander=GraphExpander(
                lambda keys, user_id: {},
                entity_loader=lambda keys, user_id, file_ids: {("7", 38): [entity]},
            ),
        )
        result = pipeline.run(
            RetrievalRequest(
                query="who filed the declaration?",
                top_k=4,
                file_ids=(7, 8),
                user_id=1,
                trace=True,
            )
        )

        self.assertEqual(
            [(c.file_id, c.chunk_index) for c in result.chunks], [("7", 38), ("8", 4)]
        )
        self.assertIn(
            '[S2] affidavit.pdf · p. 2 · Affidavit · shares "Wilkins Abbs" with [S1]',
            result.blocks[1],
        )
        added = result.trace.to_payload()["expand"]["added"][0]
        self.assertEqual((added["via"], added["viaKind"]), ("Wilkins Abbs", "entity"))

    def test_build_pipeline_wires_the_entity_loader_behind_its_flag(self):
        with patch("core.services.rag.pipeline.get_retriever"):
            self.assertIsNotNone(build_pipeline("document").expander._entity_loader)
            with override_settings(RAG_ENTITY_HOPS_ENABLED="false"):
                self.assertIsNone(build_pipeline("document").expander._entity_loader)

    def test_anchors_entity_hop_under_source_file_not_hop_file(self):
        """An entity hop's source lives in ``via.source_file_id``, which can
        differ from the hop's own ``file_id``. If anchoring keyed off the
        hop's own file, a same-indexed chunk in *that* file could wrongly
        capture it instead of the real source in the other file."""
        real_source = RetrievedChunk(
            text="Declaration by Wilkins Abbs.",
            source_ref="petition.pdf",
            score=0.95,
            chunk_index=38,
            source_type="document",
            file_id="7",
            file_name="petition.pdf",
        )
        unrelated = RetrievedChunk(
            text="An unrelated passage that happens to share a chunk index.",
            source_ref="other.pdf",
            score=0.80,
            chunk_index=38,
            source_type="document",
            file_id="8",
            file_name="other.pdf",
        )
        filler = RetrievedChunk(
            text="Another unrelated passage.",
            source_ref="petition.pdf",
            score=0.60,
            chunk_index=50,
            source_type="document",
            file_id="7",
            file_name="petition.pdf",
        )
        entity_hop = RetrievedChunk(
            text="Affidavit of Wilkins Abbs…",
            source_ref="affidavit.pdf",
            score=0.40,
            chunk_index=4,
            source_type="document",
            file_id="8",
            file_name="affidavit.pdf",
            via=ReferenceHop(
                source_chunk_index=38,
                kind="entity",
                key="wilkins abbs",
                raw_text="Wilkins Abbs",
                source_file_id="7",
            ),
        )

        ordered = RetrievalPipeline._anchor_hops(
            [real_source, unrelated, filler, entity_hop]
        )

        self.assertEqual(
            [(c.file_id, c.chunk_index) for c in ordered],
            [("7", 38), ("8", 4), ("8", 38), ("7", 50)],
        )
        self.assertEqual([c.score for c in ordered], [0.95, 0.40, 0.80, 0.60])

    def test_anchoring_does_not_lose_a_chained_hop(self):
        source = hit(27)
        first = RetrievedChunk(
            text="a",
            source_ref="book.pdf",
            score=0.5,
            chunk_index=1,
            source_type="document",
            file_id="7",
            file_name="book.pdf",
            via=ReferenceHop(27, "section", "2", "section 2"),
        )
        second = RetrievedChunk(
            text="b",
            source_ref="book.pdf",
            score=0.4,
            chunk_index=2,
            source_type="document",
            file_id="7",
            file_name="book.pdf",
            via=ReferenceHop(1, "section", "3", "section 3"),
        )
        ordered = RetrievalPipeline._anchor_hops([source, first, second])
        self.assertEqual([c.chunk_index for c in ordered], [27, 1, 2])
