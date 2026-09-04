from types import SimpleNamespace

from django.test import SimpleTestCase

from core.services.rag.dtos import RetrievedChunk
from core.services.rag.expander import GraphExpander


def hit(index, score=0.9, file_id="7"):
    return RetrievedChunk(
        text=f"chunk {index}",
        source_ref="book.pdf",
        score=score,
        chunk_index=index,
        source_type="document",
        file_id=file_id,
        file_name="book.pdf",
    )


def hop(target, kind="section", key="7.2", raw="see section 7.2"):
    return SimpleNamespace(
        kind=kind,
        key=key,
        raw_text=raw,
        chunk_index=target,
        text=f"target {target}",
        page_start=200 + target,
        page_end=200 + target,
        section="7.2 Collisions",
        file_name="book.pdf",
    )


class GraphExpanderTests(SimpleTestCase):
    def test_follows_hops_and_tags_them(self):
        loader = lambda keys, user_id: {("7", 38): [hop(31)]}
        added = GraphExpander(loader).expand([hit(38)], reranker_on=True, user_id=1)

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].chunk_index, 31)
        self.assertEqual(added[0].score, 0.9)
        self.assertEqual(
            (added[0].page_start, added[0].section), (231, "7.2 Collisions")
        )
        self.assertEqual(added[0].via.source_chunk_index, 38)
        self.assertEqual(added[0].via.raw_text, "see section 7.2")

    def test_caps_and_dedupes(self):
        loader = lambda keys, user_id: {
            ("7", 1): [hop(10), hop(11, key="a"), hop(12, key="b")],
            ("7", 2): [hop(10), hop(13, key="c")],
            ("7", 3): [hop(14, key="d"), hop(15, key="e"), hop(16, key="f")],
            ("7", 4): [hop(17, key="g"), hop(18, key="h")],
        }
        pool = [hit(1), hit(2), hit(3), hit(4), hit(10)]
        added = GraphExpander(loader, per_hit=2, max_added=6).expand(
            pool, reranker_on=True, user_id=1
        )

        self.assertEqual([c.chunk_index for c in added], [11, 12, 13, 14, 15, 17])

    def test_unranked_hops_sit_below_their_source(self):
        added = GraphExpander(lambda keys, user_id: {("7", 38): [hop(31)]}).expand(
            [hit(38, score=1.0)], reranker_on=False, user_id=1
        )
        self.assertAlmostEqual(added[0].score, 0.9)

    def test_loader_failure_or_library_pool_adds_nothing(self):
        def boom(keys, user_id):
            raise RuntimeError("db down")

        self.assertEqual(
            GraphExpander(boom).expand([hit(38)], reranker_on=True, user_id=1), []
        )
        library = RetrievedChunk(
            text="x", source_ref="lib", score=0.9, chunk_index=1, source_type="library"
        )
        calls = []
        self.assertEqual(
            GraphExpander(lambda keys, user_id: calls.append(keys) or {}).expand(
                [library], reranker_on=True, user_id=1
            ),
            [],
        )
        self.assertEqual(calls, [])

    def test_bad_loader_result_adds_nothing(self):
        self.assertEqual(
            GraphExpander(lambda keys, user_id: None).expand(
                [hit(38)], reranker_on=True, user_id=1
            ),
            [],
        )


def entity_hop(target, file_id="8", key="wilkins abbs", raw="Wilkins Abbs"):
    return SimpleNamespace(
        kind="entity",
        key=key,
        raw_text=raw,
        chunk_index=target,
        text=f"target {target}",
        page_start=2,
        page_end=2,
        section="Affidavit",
        file_name="affidavit.pdf",
        entity_kind="person",
        file_id=file_id,
    )


class EntityHopTests(SimpleTestCase):
    def test_entity_hop_reaches_another_file_with_provenance(self):
        expander = GraphExpander(
            lambda keys, user_id: {},
            entity_loader=lambda keys, user_id, file_ids: {("7", 38): [entity_hop(4)]},
        )
        added = expander.expand([hit(38)], reranker_on=True, user_id=1, file_ids=(7, 8))

        self.assertEqual(len(added), 1)
        self.assertEqual(
            (added[0].file_id, added[0].chunk_index, added[0].file_name),
            ("8", 4, "affidavit.pdf"),
        )
        self.assertEqual(
            (added[0].via.kind, added[0].via.key, added[0].via.raw_text),
            ("entity", "wilkins abbs", "Wilkins Abbs"),
        )

    def test_entity_hops_need_two_selected_files(self):
        calls = []
        expander = GraphExpander(
            lambda keys, user_id: {},
            entity_loader=lambda keys, user_id, file_ids: calls.append(file_ids) or {},
        )
        self.assertEqual(
            expander.expand([hit(38)], reranker_on=True, user_id=1, file_ids=(7,)), []
        )
        self.assertEqual(calls, [])

    def test_pointer_hops_come_first_and_share_the_caps(self):
        expander = GraphExpander(
            lambda keys, user_id: {("7", 38): [hop(31), hop(32, key="b")]},
            entity_loader=lambda keys, user_id, file_ids: {("7", 38): [entity_hop(4)]},
            per_hit=2,
        )
        added = expander.expand([hit(38)], reranker_on=True, user_id=1, file_ids=(7, 8))
        self.assertEqual(
            [(c.file_id, c.chunk_index) for c in added], [("7", 31), ("7", 32)]
        )

    def test_entity_loader_failure_keeps_pointer_hops(self):
        def boom(keys, user_id, file_ids):
            raise RuntimeError("db down")

        expander = GraphExpander(
            lambda keys, user_id: {("7", 38): [hop(31)]}, entity_loader=boom
        )
        added = expander.expand([hit(38)], reranker_on=True, user_id=1, file_ids=(7, 8))
        self.assertEqual([c.chunk_index for c in added], [31])

    def test_entity_hop_without_a_target_file_id_is_skipped(self):
        expander = GraphExpander(
            lambda keys, user_id: {},
            entity_loader=lambda keys, user_id, file_ids: {
                ("7", 38): [entity_hop(4, file_id="")]
            },
        )
        added = expander.expand([hit(38)], reranker_on=True, user_id=1, file_ids=(7, 8))
        self.assertEqual(added, [])
