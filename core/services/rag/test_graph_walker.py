from django.test import SimpleTestCase

from core.services.rag.graph_walker import (
    WalkEntityLink,
    WalkGraph,
    build_graph,
    find_connections,
)


class FindConnectionsTests(SimpleTestCase):
    def network(self, links):
        return build_graph(
            WalkGraph(
                links=[
                    WalkEntityLink(("f", chunk), entity, str(entity))
                    for chunk, entity in links
                ]
            )
        )

    def test_rare_shared_name_ranks_above_a_common_one(self):
        network = self.network(
            [
                (1, 1),
                (1, 9),
                (1, 7),
                (2, 2),
                (2, 9),
                (2, 7),
                (3, 9),
                (4, 9),
                (5, 9),
            ]
        )
        direct, through = find_connections(network, ("entity", 1), ("entity", 2))
        self.assertEqual(direct, [])
        self.assertEqual([path[2] for path in through], [("entity", 7), ("entity", 9)])

    def test_chunk_naming_both_is_a_direct_route(self):
        network = self.network([(1, 1), (1, 2)])
        direct, through = find_connections(network, ("entity", 1), ("entity", 2))
        self.assertEqual(direct, [[("entity", 1), ("chunk", ("f", 1)), ("entity", 2)]])
        self.assertEqual(through, [])
