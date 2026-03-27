"""
Execution Routing — synchronous routing decisions using pre-loaded graph.

Determines whether a node should execute (based on upstream routing)
and gathers dependency results from in-memory execution state.
No database queries — all data comes from WorkflowGraph.
"""
import logging
from typing import Dict

from workflows.handlers.base import ExecutionNode, NodeExecutionResult
from workflows.services.workflow_graph import WorkflowGraph

logger = logging.getLogger(__name__)


def should_execute(
    graph: WorkflowGraph, node: ExecutionNode,
    node_results: Dict[str, NodeExecutionResult]
) -> bool:
    """Check if node should execute based on routing decisions."""
    if node.type == 'start':
        return True

    incoming = graph.edge_map_by_target.get(node.id, [])
    if not incoming:
        return True

    # For structuredOutput: execute if any predecessor not skipped
    if node.type == 'structuredOutput':
        for e in incoming:
            if e.source in node_results:
                result = node_results[e.source]
                is_skipped = result.metadata and result.metadata.get('skipped')
                if not is_skipped:
                    return True
        logger.info(f"Skipping structuredOutput {node.id}: all predecessors skipped")
        return False

    # Evaluate routing for regular nodes
    has_routing_edge = False
    any_routing_match = False
    any_non_routing_valid = False

    for edge in incoming:
        source_node_id = edge.source
        source_node = graph.node_map.get(source_node_id)
        if not source_node:
            continue

        if source_node_id not in node_results:
            continue

        source_result = node_results[source_node_id]
        is_skipped = source_result.metadata and source_result.metadata.get('skipped')

        if source_node.node_type == 'structuredOutput':
            edge_handle = getattr(edge, 'source_handle', None)
            selected_route = source_result.metadata.get('selected_route') if source_result.metadata else None

            if edge_handle and selected_route:
                has_routing_edge = True
                expected = f"output-{selected_route}"
                if edge_handle == expected:
                    any_routing_match = True
                logger.info(f"Routing check: edge_handle={edge_handle}, expected={expected}, match={edge_handle == expected}")
            else:
                any_non_routing_valid = any_non_routing_valid or (not is_skipped)
            continue

        if not is_skipped:
            any_non_routing_valid = True

    if has_routing_edge:
        result = any_routing_match
    else:
        result = any_non_routing_valid

    logger.info(f"Routing decision for {node.id}: execute={result}, has_routing={has_routing_edge}, match={any_routing_match}, non_routing={any_non_routing_valid}")
    return result


def get_dep_results(
    graph: WorkflowGraph, node: ExecutionNode,
    node_results: Dict[str, NodeExecutionResult]
) -> Dict[str, Dict]:
    """Get dependency results from in-memory dict for handler context."""
    dep_ids = {e.source for e in graph.edge_map_by_target.get(node.id, [])}
    if not dep_ids:
        return {}

    results = {}
    for dep_id in dep_ids:
        if dep_id in node_results:
            r = node_results[dep_id]
            results[dep_id] = {
                'output': r.output,
                'metadata': r.metadata or {},
                'node_type': graph.type_map.get(dep_id)
            }

    # Handle chatOutput nodes (copy from source step)
    for dep_id in dep_ids:
        if graph.type_map.get(dep_id) == 'chatOutput' and dep_id not in results:
            src = next(
                (e.source for e in graph.edge_map_by_target.get(dep_id, [])),
                None
            )
            if src and src in node_results:
                r = node_results[src]
                results[dep_id] = {
                    'output': r.output,
                    'metadata': r.metadata or {},
                    'node_type': 'chatOutput'
                }

    return results
