"""
Routing evaluation utility for workflow execution.

This module provides utilities for evaluating whether nodes should execute
based on structured output routing constraints.
"""
import logging
from typing import Dict, Any
from workflows.handlers.base import ExecutionNode, NodeExecutionResult


logger = logging.getLogger(__name__)


class RoutingEvaluator:
    """
    Utility for evaluating routing constraints and determining node execution.

    Handles routing logic for:
    - Structured output node routing (source_handle matching)
    - Structured step routing (output-based routing)
    - Regular non-routing edges
    """

    @staticmethod
    def should_execute_node(
        node: ExecutionNode,
        node_results: Dict[str, NodeExecutionResult],
        nodes: list,
        edges: list
    ) -> bool:
        """
        Determine if a node should be executed based on routing decisions.

        Args:
            node: The node to check
            node_results: Results from previously executed nodes
            nodes: All workflow nodes
            edges: All workflow edges

        Returns:
            True if node should be executed, False if it should be skipped
        """
        # Always execute start nodes
        if node.type == 'start':
            return True

        # Evaluate routing constraints across all incoming edges
        incoming_edges = [edge for edge in edges if edge.target == node.id]

        # For structuredOutput nodes:
        # Execute if at least one predecessor is NOT skipped
        # Skip if ALL predecessors are skipped (cascade skip)
        if node.type == 'structuredOutput':
            for edge in incoming_edges:
                source_node_id = edge.source
                if source_node_id in node_results:
                    source_result = node_results[source_node_id]
                    is_skipped = bool(
                        getattr(source_result, 'metadata', None) and
                        source_result.metadata.get('skipped')
                    )
                    if not is_skipped:
                        # At least one predecessor is not skipped - execute this node
                        return True
            # All predecessors are skipped (or no results yet) - skip this node
            logger.info(
                f"Skipping {node.type} node {node.id}: all predecessors are skipped"
            )
            return False

        has_routing_edge = False
        any_routing_match = False
        any_non_routing_valid = False
        any_source_available = False

        for edge in incoming_edges:
            source_node_id = edge.source
            source_node = next((n for n in nodes if n.node_id == source_node_id), None)
            if not source_node:
                continue

            # Only consider processed sources
            if source_node_id not in node_results:
                continue

            source_result = node_results[source_node_id]

            # Track if any non-skipped source exists
            is_skipped = bool(
                getattr(source_result, 'metadata', None) and
                source_result.metadata.get('skipped')
            )
            if not is_skipped:
                any_source_available = True

            # Evaluate structuredOutput routing (routing node)
            if source_node.node_type == 'structuredOutput':
                evaluation_result = RoutingEvaluator._evaluate_structured_output_routing(
                    edge, source_result, is_skipped
                )
                has_routing_edge = evaluation_result['has_routing']
                any_routing_match = any_routing_match or evaluation_result['matches']
                any_non_routing_valid = any_non_routing_valid or evaluation_result['non_routing_valid']

                logger.info(
                    f"StructuredOutput routing for target node {node.id}: "
                    f"has_routing={has_routing_edge}, matches={evaluation_result['matches']}, "
                    f"edge_handle={edge.source_handle}"
                )
                continue

            # Evaluate structured step routing (legacy step-based routing)
            if source_node.node_type == 'step' and edge.source_handle:
                evaluation_result = RoutingEvaluator._evaluate_structured_step_routing(
                    edge, source_result, source_node_id, node.id, is_skipped
                )
                has_routing_edge = evaluation_result['has_routing']
                any_routing_match = any_routing_match or evaluation_result['matches']
                any_non_routing_valid = any_non_routing_valid or evaluation_result['non_routing_valid']
                continue

            # Non-routing edge (e.g., chatOutput -> step)
            any_non_routing_valid = any_non_routing_valid or (not is_skipped)

        # Decision logic
        should_execute = False
        if has_routing_edge:
            should_execute = any_routing_match
        elif any_non_routing_valid:
            should_execute = True
        else:
            should_execute = False

        logger.info(
            f"Routing decision for node {node.id}: execute={should_execute}, "
            f"has_routing_edge={has_routing_edge}, any_routing_match={any_routing_match}, "
            f"any_non_routing_valid={any_non_routing_valid}"
        )

        return should_execute

    @staticmethod
    def _evaluate_structured_output_routing(
        edge: Any,
        source_result: NodeExecutionResult,
        is_skipped: bool
    ) -> Dict[str, Any]:
        """
        Evaluate routing for structuredOutput node sources.

        StructuredOutput nodes use route-{name} handle format with selected_route metadata.

        Args:
            edge: The edge being evaluated
            source_result: Result from the structured output node
            is_skipped: Whether the source was skipped

        Returns:
            Dict with routing evaluation results
        """
        edge_handle = edge.source_handle
        selected_route = None

        # Check for selected_route in metadata
        if getattr(source_result, 'metadata', None):
            selected_route = source_result.metadata.get('selected_route')
            logger.info(
                f"StructuredOutput metadata check: metadata={source_result.metadata}, "
                f"selected_route={selected_route}, edge_handle={edge_handle}"
            )

        if edge_handle and selected_route is not None:
            # StructuredOutput nodes use output-{name} format (e.g., output-good, output-bad)
            # Handle routing edge
            expected = f"output-{selected_route}"
            match = (edge_handle == expected)

            logger.debug(
                f"Routing via structuredOutput: selected_route='{selected_route}', "
                f"edge_handle='{edge_handle}', match={match}"
            )

            return {
                'has_routing': True,
                'matches': match,
                'non_routing_valid': False
            }
        else:
            # No handle or decision; treat as non-routing valid if source not skipped
            return {
                'has_routing': False,
                'matches': False,
                'non_routing_valid': not is_skipped
            }

    @staticmethod
    def _evaluate_structured_step_routing(
        edge: Any,
        source_result: NodeExecutionResult,
        source_node_id: str,
        target_node_id: str,
        is_skipped: bool
    ) -> Dict[str, Any]:
        """
        Evaluate routing for structured step node sources.

        Args:
            edge: The edge being evaluated
            source_result: Result from the step node
            source_node_id: Source node ID
            target_node_id: Target node ID
            is_skipped: Whether the source was skipped

        Returns:
            Dict with routing evaluation results
        """
        edge_handle = edge.source_handle

        if isinstance(source_result.output, (str, bytes)) and edge_handle.startswith('output-'):
            # This is a routing edge
            route_value = (
                source_result.output.decode('utf-8')
                if isinstance(source_result.output, bytes)
                else str(source_result.output)
            )
            route_value = route_value.strip()
            expected = f"output-{route_value}"
            match = (edge_handle == expected)

            logger.debug(
                f"Routing via structured step {source_node_id} -> {target_node_id}: "
                f"route_value='{route_value}', edge_handle='{edge_handle}', match={match}"
            )

            return {
                'has_routing': True,
                'matches': match,
                'non_routing_valid': False
            }
        else:
            # Step source with no routing constraint on this edge
            return {
                'has_routing': False,
                'matches': False,
                'non_routing_valid': not is_skipped
            }
