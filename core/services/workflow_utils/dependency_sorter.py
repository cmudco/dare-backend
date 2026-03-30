"""
Dependency sorting utility for workflow execution.

This module provides utilities for topologically sorting workflow nodes
based on their dependencies to ensure proper execution order.

Enhanced to support start node chaining with depth-based priority and
cycle detection to prevent infinite loops.
"""
from typing import List, Set, Dict, Tuple
from collections import deque
from workflows.handlers.base import ExecutionNode
import logging

logger = logging.getLogger(__name__)


class DependencySorter:
    """
    Utility for sorting workflow nodes by dependencies.

    Handles topological sorting with special logic for:
    - Multi-input nodes (wait for ALL dependencies)
    - Structured output nodes (single input dependency)
    - Priority-based sorting within dependency levels
    - Start node chaining (depth-based execution order)
    - Cycle detection to prevent infinite loops
    """

    @staticmethod
    def sort_nodes_by_dependencies(
        execution_nodes: List[ExecutionNode],
        edges: List
    ) -> List[ExecutionNode]:
        """
        Sort nodes based on their dependencies to ensure proper execution order.

        Structured output nodes must run before nodes that depend on their routing decisions.
        Multi-input nodes wait for ALL their dependencies before execution.
        Start node chains execute in depth order (chain 1 before chain 2).

        Args:
            execution_nodes: List of nodes to sort
            edges: List of workflow edges defining dependencies

        Returns:
            Topologically sorted list of execution nodes

        Raises:
            ValueError: If circular dependency is detected
        """
        # Detect circular dependencies before attempting sort
        if DependencySorter._detect_cycles(execution_nodes, edges):
            raise ValueError(
                "Circular dependency detected in workflow graph. "
                "Please check start node chain connections to avoid infinite loops."
            )

        # Build dependency map: node_id -> set of nodes it depends on
        dependencies = {node.id: set() for node in execution_nodes}

        for edge in edges:
            dependencies[edge.target].add(edge.source)

        # Calculate depth for each node (distance from root start nodes)
        # This enables proper start node chaining: Chain 1 completes before Chain 2
        node_depths = DependencySorter._calculate_node_depths(execution_nodes, edges)

        # Topological sort with special handling for routing node dependencies
        sorted_nodes = []
        remaining_nodes = execution_nodes.copy()

        while remaining_nodes:
            # Find nodes with no unmet dependencies
            ready_nodes = DependencySorter._find_ready_nodes(
                remaining_nodes, sorted_nodes, dependencies, edges
            )

            if not ready_nodes:
                # Fallback: if no nodes are ready (circular dependency), take start nodes
                ready_nodes = [n for n in remaining_nodes if n.type == 'start']
                if not ready_nodes:
                    ready_nodes = [remaining_nodes[0]]  # Emergency fallback

            # Sort ready nodes by depth, then priority, then step number
            # Lower depth = executes first (ensures Chain 1 before Chain 2)
            ready_nodes.sort(
                key=lambda n: (
                    node_depths.get(n.id, 0),
                    DependencySorter._get_type_priority(n),
                    n.id
                )
            )

            # Add the first ready node to execution order
            next_node = ready_nodes[0]
            sorted_nodes.append(next_node)
            remaining_nodes.remove(next_node)

        return sorted_nodes

    @staticmethod
    def _find_ready_nodes(
        remaining_nodes: List[ExecutionNode],
        sorted_nodes: List[ExecutionNode],
        dependencies: Dict[str, Set[str]],
        edges: List
    ) -> List[ExecutionNode]:
        """
        Find nodes that are ready to execute (all dependencies met).

        Args:
            remaining_nodes: Nodes not yet sorted
            sorted_nodes: Nodes already sorted
            dependencies: Dependency map
            edges: Workflow edges

        Returns:
            List of nodes ready for execution
        """
        ready_nodes = []
        executed_deps = {n.id for n in sorted_nodes}

        for node in remaining_nodes:
            deps = dependencies[node.id]

            if node.type == 'step':
                # For step nodes, check if they have multiple inputs
                incoming_edges = [e for e in edges if e.target == node.id]

                if len(incoming_edges) > 1:
                    # Multi-input step node: ensure ALL incoming edges are from executed nodes
                    all_sources_ready = all(e.source in executed_deps for e in incoming_edges)

                    if all_sources_ready and deps.issubset(executed_deps):
                        ready_nodes.append(node)
                else:
                    # Single-input step node: regular dependency check
                    if deps.issubset(executed_deps):
                        ready_nodes.append(node)

            else:
                # Regular dependency check for other node types
                if deps.issubset(executed_deps):
                    ready_nodes.append(node)

        return ready_nodes

    @staticmethod
    def _get_type_priority(node: ExecutionNode) -> int:
        """
        Get priority value for node type.

        Args:
            node: Execution node

        Returns:
            Priority value (lower = higher priority)
        """
        return {
            'start': 0,
            'step': 1,
            'chatOutput': 2,
            'structuredOutput': 3
        }.get(node.type, 999)

    @staticmethod
    def _calculate_node_depths(
        execution_nodes: List[ExecutionNode],
        edges: List
    ) -> Dict[str, int]:
        """
        Calculate depth of each node from root start nodes using BFS.

        Depth represents the distance from root start nodes (start nodes with no
        incoming edges). This enables proper start node chaining where Chain 1
        (depth 0) completes before Chain 2 (depth N) begins.

        Args:
            execution_nodes: List of nodes in the workflow
            edges: List of workflow edges

        Returns:
            Dictionary mapping node_id to depth value
        """
        depths = {node.id: 0 for node in execution_nodes}

        # Build adjacency list for forward traversal
        graph = {node.id: [] for node in execution_nodes}
        for edge in edges:
            graph[edge.source].append(edge.target)

        # Count incoming edges for each node
        incoming_counts = {node.id: 0 for node in execution_nodes}
        for edge in edges:
            incoming_counts[edge.target] += 1

        # Find root start nodes (start nodes with no incoming edges)
        root_starts = [
            node.id for node in execution_nodes
            if node.type == 'start' and incoming_counts[node.id] == 0
        ]

        # If no root start nodes found, treat all start nodes as depth 0
        if not root_starts:
            root_starts = [node.id for node in execution_nodes if node.type == 'start']
            logger.warning("No root start nodes found, treating all start nodes as depth 0")

        # BFS to calculate depths from root start nodes
        queue = deque((node_id, 0) for node_id in root_starts)
        visited = set()

        while queue:
            node_id, depth = queue.popleft()

            # Skip if already visited with a shorter path
            if node_id in visited:
                continue

            visited.add(node_id)
            depths[node_id] = depth

            # Add neighbors with incremented depth
            for neighbor in graph[node_id]:
                if neighbor not in visited:
                    queue.append((neighbor, depth + 1))

        logger.debug(f"Calculated node depths: {depths}")
        return depths

    @staticmethod
    def _detect_cycles(
        execution_nodes: List[ExecutionNode],
        edges: List
    ) -> bool:
        """
        Detect circular dependencies in workflow graph using DFS.

        Args:
            execution_nodes: List of nodes in the workflow
            edges: List of workflow edges

        Returns:
            True if cycle detected, False otherwise
        """
        # Build adjacency list
        graph = {node.id: [] for node in execution_nodes}
        for edge in edges:
            graph[edge.source].append(edge.target)

        visited = set()
        rec_stack = set()

        def has_cycle_dfs(node_id: str) -> bool:
            """DFS helper to detect cycles"""
            visited.add(node_id)
            rec_stack.add(node_id)

            # Check all neighbors
            for neighbor in graph.get(node_id, []):
                if neighbor not in visited:
                    if has_cycle_dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    # Back edge detected - cycle found
                    logger.error(f"Circular dependency detected: {node_id} -> {neighbor}")
                    return True

            rec_stack.remove(node_id)
            return False

        # Check all nodes for cycles
        for node in execution_nodes:
            if node.id not in visited:
                if has_cycle_dfs(node.id):
                    return True

        return False
