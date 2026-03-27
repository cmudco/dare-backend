"""
Workflow Graph — execution-time graph loading and topological ordering.

Loads nodes and edges once per execution, builds lookup dicts,
and produces a topologically sorted list of executable nodes.
"""
import heapq
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from channels.db import database_sync_to_async

from workflows.handlers.base import ExecutionNode
from workflows.models import WorkflowNode

logger = logging.getLogger(__name__)

NON_EXECUTABLE_TYPES = frozenset({'notes', 'chatOutput'})

TYPE_ORDER = {'start': 0, 'file': 1, 'step': 2, 'structuredOutput': 3}


@dataclass
class WorkflowGraph:
    """Pre-loaded workflow graph data. Loaded once, passed everywhere."""
    nodes: List[WorkflowNode]
    edges: list
    node_map: Dict[str, WorkflowNode] = field(default_factory=dict)
    edge_map_by_target: Dict[str, list] = field(default_factory=lambda: defaultdict(list))
    type_map: Dict[str, str] = field(default_factory=dict)


async def load_graph(workflow) -> WorkflowGraph:
    """Load all nodes and edges once. Build lookup dicts."""
    def _load():
        nodes = list(workflow.nodes.all())
        for node in nodes:
            node._prefetched_data_object = node.data_object
        edges = list(workflow.edges.all())
        return nodes, edges

    db_nodes, edges = await database_sync_to_async(_load)()

    node_map = {n.node_id: n for n in db_nodes}
    type_map = {n.node_id: n.node_type for n in db_nodes}
    edge_map_by_target: Dict[str, list] = defaultdict(list)
    for e in edges:
        edge_map_by_target[e.target].append(e)

    return WorkflowGraph(
        nodes=db_nodes,
        edges=edges,
        node_map=node_map,
        edge_map_by_target=edge_map_by_target,
        type_map=type_map,
    )


def get_ordered_exec_nodes(graph: WorkflowGraph) -> List[ExecutionNode]:
    """Topological sort of executable nodes using Kahn's algorithm with heapq."""
    exec_nodes = [
        ExecutionNode(
            id=node.node_id,
            type=node.node_type,
            label=getattr(node._prefetched_data_object, 'label', '') or '',
            db_node=node,
        )
        for node in graph.nodes
        if node.node_type not in NON_EXECUTABLE_TYPES
    ]

    exec_map = {n.id: n for n in exec_nodes}
    in_deg = {n.id: 0 for n in exec_nodes}
    for e in graph.edges:
        if e.target in in_deg:
            in_deg[e.target] += 1

    # heapq entries: (type_priority, node_id)
    heap = [
        (TYPE_ORDER.get(exec_map[nid].type, 99), nid)
        for nid, d in in_deg.items() if d == 0
    ]
    heapq.heapify(heap)

    result = []
    while heap:
        _, nid = heapq.heappop(heap)
        result.append(exec_map[nid])
        for e in graph.edges:
            if e.source == nid and e.target in in_deg:
                in_deg[e.target] -= 1
                if in_deg[e.target] == 0:
                    heapq.heappush(
                        heap,
                        (TYPE_ORDER.get(exec_map[e.target].type, 99), e.target)
                    )

    return result
