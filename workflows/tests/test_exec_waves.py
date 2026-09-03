"""Dependency waves: what the runner executes concurrently."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from workflows.services.workflow_graph import WorkflowGraph, get_exec_waves


def _graph(nodes, edges):
    node_objs = [
        SimpleNamespace(node_id=nid, node_type=ntype, _prefetched_data_object=None)
        for nid, ntype in nodes
    ]
    edge_objs = [SimpleNamespace(source=s, target=t) for s, t in edges]
    graph = WorkflowGraph(nodes=node_objs, edges=edge_objs)
    for node in node_objs:
        graph.node_map[node.node_id] = node
        graph.type_map[node.node_id] = node.node_type
    for edge in edge_objs:
        graph.edge_map_by_target[edge.target].append(edge)
    return graph


class ExecWavesTests(SimpleTestCase):
    def test_panel_graph_runs_responders_together_and_chairman_after(self):
        graph = _graph(
            nodes=[
                ("start", "start"),
                ("responder-1", "step"),
                ("responder-2", "step"),
                ("responder-3", "step"),
                ("chairman", "step"),
                ("chairman-output", "chatOutput"),
            ],
            edges=[
                ("start", "responder-1"),
                ("start", "responder-2"),
                ("start", "responder-3"),
                ("responder-1", "chairman"),
                ("responder-2", "chairman"),
                ("responder-3", "chairman"),
                ("chairman", "chairman-output"),
            ],
        )
        waves = [[n.id for n in wave] for wave in get_exec_waves(graph)]
        self.assertEqual(
            waves,
            [["start"], ["responder-1", "responder-2", "responder-3"], ["chairman"]],
        )

    def test_pass_through_output_nodes_do_not_break_dependencies(self):
        graph = _graph(
            nodes=[
                ("start", "start"),
                ("a", "step"),
                ("a-out", "chatOutput"),
                ("b", "step"),
            ],
            edges=[("start", "a"), ("a", "a-out"), ("a-out", "b")],
        )
        waves = [[n.id for n in wave] for wave in get_exec_waves(graph)]
        self.assertEqual(waves, [["start"], ["a"], ["b"]])
