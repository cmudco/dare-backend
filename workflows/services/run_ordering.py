from __future__ import annotations

from typing import Iterable

from workflows.models import Workflow


RUN_STEP_NODE_TYPES = {"step", "structuredOutput"}
NON_EXECUTABLE_NODE_TYPES = {"notes"}
NODE_TYPE_PRIORITY = {
    "start": 0,
    "file": 1,
    "step": 2,
    "structuredOutput": 3,
    "chatOutput": 4,
}


def get_workflow_run_order_map(workflow: Workflow) -> dict[str, int]:
    """Return graph-derived execution order for nodes that create WorkflowRunStep rows."""
    nodes = [
        node
        for node in workflow.nodes.all()
        if node.node_type not in NON_EXECUTABLE_NODE_TYPES
    ]
    edges = list(workflow.edges.all())

    node_map = {node.node_id: node for node in nodes}
    in_degree = {node.node_id: 0 for node in nodes}
    outgoing: dict[str, list[str]] = {node.node_id: [] for node in nodes}

    for edge in edges:
        if edge.source not in node_map or edge.target not in node_map:
            continue
        outgoing[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    ready = [node_id for node_id, degree in in_degree.items() if degree == 0]
    ordered_node_ids: list[str] = []

    while ready:
        ready.sort(key=_node_sort_key(node_map))
        node_id = ready.pop(0)
        ordered_node_ids.append(node_id)
        for target_node_id in outgoing[node_id]:
            in_degree[target_node_id] -= 1
            if in_degree[target_node_id] == 0:
                ready.append(target_node_id)

    return {
        node_id: index
        for index, node_id in enumerate(
            (
                node_id
                for node_id in ordered_node_ids
                if node_map[node_id].node_type in RUN_STEP_NODE_TYPES
            ),
            start=1,
        )
    }


def _node_sort_key(node_map) -> callable:
    def sort_key(node_id: str) -> tuple[int, str]:
        node = node_map[node_id]
        return (NODE_TYPE_PRIORITY.get(node.node_type, 99), node.node_id)

    return sort_key
