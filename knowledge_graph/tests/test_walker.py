from django.test import SimpleTestCase

from core.services.rag.dtos import RetrievedChunk
from core.services.rag.graph_walker import (
    GraphWalker,
    WalkChunk,
    WalkEntityLink,
    WalkGraph,
    WalkRelation,
)

MAPPA, TOKYO, AOT = 1, 2, 3


def chunk(file_id, index, score=1.0):
    return RetrievedChunk(
        text=f"text {file_id}-{index}",
        source_ref=f"file{file_id}",
        score=score,
        chunk_index=index,
        source_type="document",
        file_id=str(file_id),
        file_name=f"file{file_id}",
    )


def walker(graph):
    def load_chunks(keys, user_id):
        return {
            key: WalkChunk(key, f"text {key}", f"file{key[0]}", 1, 1, "", "")
            for key in keys
        }

    def resolve(user_id, names, loaded):
        return {name: AOT for name in names if name == "AoT"}

    return GraphWalker(lambda user_id, file_ids: graph, resolve, load_chunks)


def network():
    links = [
        WalkEntityLink(("1", 0), AOT, "AoT"),
        WalkEntityLink(("1", 0), MAPPA, "MAPPA"),
        WalkEntityLink(("2", 0), MAPPA, "Studio MAPPA"),
        WalkEntityLink(("3", 0), MAPPA, "MAPPA"),
    ]
    links += [WalkEntityLink((str(n), 0), TOKYO, "Tokyo") for n in range(1, 30)]
    return WalkGraph(
        links=links,
        entity_names={MAPPA: "MAPPA", TOKYO: "Tokyo", AOT: "Attack on Titan"},
    )


class GraphWalkerTests(SimpleTestCase):
    def test_reaches_new_chunks_through_a_shared_rare_entity(self):
        result = walker(network()).walk([chunk(1, 0)], 1, [1, 2, 3], ["AoT"])
        reached = {hop.file_id for hop in result.hops}
        self.assertTrue({"2", "3"} <= reached)

    def test_rare_entity_outranks_common_one(self):
        result = walker(network()).walk([chunk(1, 0)], 1, [1, 2, 3], ["AoT"])
        ranked = sorted(result.hops, key=lambda hop: -hop.graph_score)
        self.assertEqual({ranked[0].file_id, ranked[1].file_id}, {"2", "3"})

    def test_path_names_the_bridge_and_records_the_alias(self):
        result = walker(network()).walk([chunk(1, 0)], 1, [1, 2, 3], ["AoT"])
        hop = next(h for h in result.hops if h.file_id == "2")
        labels = [step.label for step in hop.via.path]
        self.assertIn("MAPPA", labels)
        self.assertEqual(hop.via.path[-1].kind, "chunk")

    def test_chunk_already_in_pool_is_marked_not_duplicated(self):
        pool = [chunk(1, 0), chunk(9, 0), chunk(8, 0), chunk(2, 0, score=0.1)]
        result = walker(network()).walk(pool, 1, [1, 2, 3], ["AoT"])
        self.assertNotIn(("2", 0), {(h.file_id, h.chunk_index) for h in result.hops})
        marked = next(c for c in result.pool if c.file_id == "2")
        self.assertIsNotNone(marked.via)

    def test_stated_relation_carries_into_the_path(self):
        graph = WalkGraph(
            links=[
                WalkEntityLink(("1", 0), AOT, "AoT"),
                WalkEntityLink(("2", 0), MAPPA, "MAPPA"),
            ],
            relations=[WalkRelation(MAPPA, AOT, "produced", 0.95)],
            entity_names={MAPPA: "MAPPA", AOT: "Attack on Titan"},
        )
        result = walker(graph).walk([chunk(1, 0)], 1, [1, 2], ["AoT"])
        step = next(s for s in result.hops[0].via.path if s.label == "MAPPA")
        self.assertEqual(step.relation, "produced")
        self.assertTrue(step.relation_reversed)

    def test_empty_graph_is_a_clean_no_op(self):
        pool = [chunk(1, 0)]
        result = walker(WalkGraph()).walk(pool, 1, [1], ["AoT"])
        self.assertEqual(result.hops, [])
        self.assertEqual(result.pool, pool)
