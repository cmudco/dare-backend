"""
Dependency sorting utility for workflow execution.

This module provides utilities for topologically sorting workflow nodes
based on their dependencies to ensure proper execution order.
"""
from typing import List, Set, Dict
from workflows.handlers.base import ExecutionNode


class DependencySorter:
    """
    Utility for sorting workflow nodes by dependencies.

    Handles topological sorting with special logic for:
    - Multi-input nodes (wait for ALL dependencies)
    - Conditional nodes (single input dependency)
    - Priority-based sorting within dependency levels
    """

    @staticmethod
    def sort_nodes_by_dependencies(
        execution_nodes: List[ExecutionNode],
        edges: List
    ) -> List[ExecutionNode]:
        """
        Sort nodes based on their dependencies to ensure proper execution order.

        Conditional nodes must run before nodes that depend on their routing decisions.
        Multi-input nodes wait for ALL their dependencies before execution.

        Args:
            execution_nodes: List of nodes to sort
            edges: List of workflow edges defining dependencies

        Returns:
            Topologically sorted list of execution nodes
        """
        # Build dependency map: node_id -> set of nodes it depends on
        dependencies = {node.id: set() for node in execution_nodes}

        for edge in edges:
            dependencies[edge.target].add(edge.source)

        # Topological sort with special handling for conditional dependencies
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

            # Sort ready nodes by priority within the same dependency level
            ready_nodes.sort(key=DependencySorter._get_priority_sort_key)

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

            elif node.type == 'conditional':
                # For conditional nodes, ensure the single input dependency is executed
                if deps.issubset(executed_deps):
                    # Additional check: ensure we have actual output from dependencies
                    has_valid_input = any(dep_id in executed_deps for dep_id in deps)
                    if has_valid_input:
                        ready_nodes.append(node)
            else:
                # Regular dependency check for other node types
                if deps.issubset(executed_deps):
                    ready_nodes.append(node)

        return ready_nodes

    @staticmethod
    def _get_priority_sort_key(node: ExecutionNode) -> tuple:
        """
        Get sort key for priority-based sorting of nodes at same dependency level.

        Args:
            node: Execution node

        Returns:
            Tuple for sorting (priority, step_number)
        """
        type_priority = {
            'start': 0,
            'step': 1,
            'chatOutput': 2,
            'conditional': 3  # After chatOutput nodes
        }.get(node.type, 999)

        return (type_priority, node.step_number or 0)
