"""
Comprehensive Workflow Validation Module

This module provides the single source of truth for all workflow validation logic.
It validates workflow graph structure, node data, and execution readiness.

Previously, validation logic was split between frontend (validateWorkflow.ts) and
backend (ExecutionValidator). This module consolidates all validation rules in one place.
"""

from typing import Tuple, List, Dict, Set
from workflows.models import Workflow


class WorkflowValidator:
    """
    Comprehensive workflow validation - single source of truth for all validation rules.

    This class replaces dual validation (frontend + backend) with backend-only validation.
    All validation logic ported from frontend's validateWorkflow.ts is consolidated here.
    """

    # Constants for route handle prefix (matches frontend constant)
    ROUTE_HANDLE_PREFIX = 'output-'

    @staticmethod
    def validate_for_execution(workflow: Workflow) -> Tuple[bool, List[str]]:
        """
        Validates that a workflow is ready for execution.

        This is the comprehensive validation that runs when user clicks "Run".
        Checks both graph structure and all execution requirements (prompt, llm, etc.).

        Args:
            workflow: The workflow to validate

        Returns:
            Tuple of (is_valid, list_of_error_messages)

        Validation Rules:
            - At least one start node exists
            - At least one step node exists
            - All nodes are reachable from a start node
            - Start nodes have title and description
            - Step nodes have prompt and llm
            - Routing nodes have prompt, llm, and 2+ routes
            - Structured output nodes have (prompt OR textInput), llm, and 2+ routes
            - All routes have unique non-empty names
            - All routes connect only to step nodes
            - At least one route is connected per structured output node
        """
        errors: List[str] = []

        # Get all nodes and edges
        nodes = list(workflow.nodes.all().prefetch_related('data_object'))
        edges = list(workflow.edges.all())

        # Build edge lookup for graph traversal
        edges_by_source = WorkflowValidator._build_edge_lookup(edges)
        edges_by_target = WorkflowValidator._build_edge_lookup_by_target(edges)
        node_lookup = {node.node_id: node for node in nodes}

        # 1. Validate start nodes
        start_errors = WorkflowValidator._validate_start_nodes(nodes)
        errors.extend(start_errors)

        # 2. Validate that at least one step node exists
        step_nodes = [n for n in nodes if n.node_type == 'step']
        if len(step_nodes) == 0:
            errors.append('At least one step is required')

        # 3. Validate graph connectivity (all nodes reachable from start nodes)
        start_nodes = [n for n in nodes if n.node_type == 'start']
        if len(start_nodes) > 0 and len(step_nodes) > 0:
            connectivity_errors = WorkflowValidator._check_graph_connectivity(
                start_nodes, step_nodes, edges_by_source
            )
            errors.extend(connectivity_errors)

        # 4. Validate step nodes (execution mode)
        step_errors = WorkflowValidator._validate_step_nodes(
            step_nodes, edges_by_source, node_lookup, for_execution=True
        )
        errors.extend(step_errors)

        # 5. Validate structured output nodes (execution mode)
        structured_nodes = [n for n in nodes if n.node_type == 'structuredOutput']
        structured_errors = WorkflowValidator._validate_structured_output_nodes(
            structured_nodes, step_nodes, edges_by_source, edges_by_target,
            node_lookup, for_execution=True
        )
        errors.extend(structured_errors)

        is_valid = len(errors) == 0
        return is_valid, errors

    @staticmethod
    def _build_edge_lookup(edges) -> Dict[str, List]:
        """Build lookup dictionary: source_node_id -> [edge objects]"""
        edge_lookup: Dict[str, List] = {}
        for edge in edges:
            if edge.source not in edge_lookup:
                edge_lookup[edge.source] = []
            edge_lookup[edge.source].append(edge)
        return edge_lookup

    @staticmethod
    def _build_edge_lookup_by_target(edges) -> Dict[str, List]:
        """Build lookup dictionary: target_node_id -> [edges]"""
        edge_lookup: Dict[str, List] = {}
        for edge in edges:
            if edge.target not in edge_lookup:
                edge_lookup[edge.target] = []
            edge_lookup[edge.target].append(edge)
        return edge_lookup

    @staticmethod
    def _validate_start_nodes(nodes) -> List[str]:
        """
        Validates all start nodes in the workflow.

        Requirements:
            - At least one start node exists
            - Each start node has a title
            - Each start node has a description
        """
        errors: List[str] = []
        start_nodes = [n for n in nodes if n.node_type == 'start']

        if len(start_nodes) == 0:
            errors.append('At least one start node is required')
            return errors

        # Validate each start node
        for idx, start_node in enumerate(start_nodes):
            data = start_node.typed_data

            # Check title
            title = getattr(data, 'title', None) or ''
            if not title.strip():
                errors.append(f"Start node {idx + 1} is missing a title")

            # Check description
            description = getattr(data, 'description', None) or ''
            if not description.strip():
                errors.append(f"Start node {idx + 1} is missing a description")

        return errors

    @staticmethod
    def _validate_step_nodes(step_nodes, edges_by_source, node_lookup, for_execution: bool) -> List[str]:
        """
        Validates all step nodes in the workflow.

        Requirements:
            - Each step connects to its output node (chatOutput)
            - For execution: Each step has a prompt selected
            - For execution: Each step has an LLM selected
        """
        errors: List[str] = []

        # Build lookup: step_number -> chatOutput nodes
        outputs_by_step: Dict[int, List] = {}
        for node in node_lookup.values():
            if node.node_type == 'chatOutput':
                step_number = getattr(node.typed_data, 'step_number', None)
                if step_number is not None:
                    if step_number not in outputs_by_step:
                        outputs_by_step[step_number] = []
                    outputs_by_step[step_number].append(node)

        for step_node in step_nodes:
            data = step_node.typed_data
            step_number = getattr(data, 'step_number', None)

            # Validate execution requirements
            if for_execution:
                # Check prompt
                prompt = getattr(data, 'prompt', None)
                if not prompt:
                    errors.append(
                        f"Step {step_number}: Missing required prompt. Please select a prompt before running."
                    )

                # Check LLM
                llm = getattr(data, 'llm', None)
                if not llm:
                    errors.append(
                        f"Step {step_number}: Missing required LLM. Please select an LLM before running."
                    )

            # Validate connection to output node
            if step_number is not None:
                outputs_for_step = outputs_by_step.get(step_number, [])
                if not outputs_for_step:
                    errors.append(
                        f"Step {step_number} must have an output node."
                    )
                    continue

                # Check if step is connected to any of its output nodes
                outgoing_targets = [edge.target for edge in edges_by_source.get(step_node.node_id, [])]
                has_output_edge = any(
                    output.node_id in outgoing_targets
                    for output in outputs_for_step
                )

                if not has_output_edge:
                    errors.append(
                        f"Step {step_number} must connect to its output node."
                    )

        return errors

    @staticmethod
    def _validate_structured_output_nodes(structured_nodes, step_nodes, edges_by_source,
                                         edges_by_target, node_lookup, for_execution: bool) -> List[str]:
        """
        Validates all structured output nodes in the workflow.

        Requirements:
            - At least 2 routes defined
            - All route names are unique and non-empty
            - Routes only connect to step nodes
            - For execution: (prompt OR textInput) is provided
            - For execution: llm is selected
            - For execution: at least one route is connected
        """
        errors: List[str] = []

        # Validate each structured output node directly
        for structured_node in structured_nodes:
            data = structured_node.typed_data
            routes = getattr(data, 'routes', []) or []
            so_step_number = getattr(data, 'step_number', None)
            so_label = f"Structured Output {so_step_number}" if so_step_number else "Structured Output node"

            # Validate execution requirements
            if for_execution:
                # Check prompt OR textInput
                prompt = getattr(data, 'prompt', None)
                text_input = getattr(data, 'text_input', None) or ''

                if not prompt and not text_input.strip():
                    errors.append(
                        f"{so_label}: Missing required input. Please provide either a prompt or text input before running."
                    )

                # Check LLM
                llm = getattr(data, 'llm', None)
                if not llm:
                    errors.append(
                        f"{so_label}: Missing required LLM selection"
                    )

            # Validate routes
            if len(routes) < 2:
                errors.append(
                    f"{so_label}: Must have at least 2 routes defined."
                )

            # Validate route names are unique and non-empty
            route_names = [r.get('name', '').strip() for r in routes if isinstance(r, dict)]
            non_empty_route_names = [name for name in route_names if name]

            if len(non_empty_route_names) != len(routes):
                errors.append(
                    f"{so_label}: All routes must have non-empty names"
                )

            if len(non_empty_route_names) != len(set(non_empty_route_names)):
                errors.append(
                    f"{so_label}: Route names must be unique."
                )

            # Get outgoing edges from the STRUCTURED OUTPUT node (not step node)
            actual_outgoing_edges = edges_by_source.get(structured_node.node_id, [])

            # Validate each route's connections
            for route in routes:
                if not isinstance(route, dict):
                    continue

                route_name = route.get('name', '').strip()
                if not route_name:
                    continue

                route_handle = f"{WorkflowValidator.ROUTE_HANDLE_PREFIX}{route_name}"
                route_connections = [
                    edge for edge in actual_outgoing_edges
                    if edge.source_handle == route_handle
                ]

                # Check if route connects to a step node (if connected)
                if len(route_connections) == 1:
                    target_node = node_lookup.get(route_connections[0].target)
                    if target_node and target_node.node_type != 'step':
                        errors.append(
                            f"{so_label} route \"{route_name}\": "
                            f"Must connect to a step node."
                        )
                elif len(route_connections) > 1:
                    errors.append(
                        f"{so_label} route \"{route_name}\": "
                        f"Can connect to only one step."
                    )

            # Require at least one route connected (for execution)
            if for_execution:
                total_route_connections = sum(
                    1 for edge in actual_outgoing_edges
                    if edge.source_handle and edge.source_handle.startswith(WorkflowValidator.ROUTE_HANDLE_PREFIX)
                )

                if total_route_connections == 0:
                    errors.append(
                        f"{so_label}: Must have at least one route connected to a step node"
                    )

        return errors

    @staticmethod
    def _check_graph_connectivity(start_nodes, step_nodes, edges_by_source) -> List[str]:
        """
        Validates that all step nodes are reachable from at least one start node.

        Uses graph traversal (BFS/DFS) to find all reachable nodes from start nodes.
        Any step node not reachable is an error.

        Args:
            start_nodes: List of start nodes
            step_nodes: List of step nodes to check
            edges_by_source: Edge lookup dictionary

        Returns:
            List of error messages for unreachable steps
        """
        errors: List[str] = []

        # Perform graph traversal from all start nodes
        reachable: Set[str] = set()
        stack = [node.node_id for node in start_nodes]

        while stack:
            current = stack.pop()
            if current in reachable:
                continue

            reachable.add(current)

            # Add all targets of this node to the stack - edges_by_source contains edge objects
            edges = edges_by_source.get(current, [])
            for edge in edges:
                if edge.target not in reachable:
                    stack.append(edge.target)

        # Check that all step nodes are reachable
        for step_node in step_nodes:
            if step_node.node_id not in reachable:
                step_data = step_node.typed_data
                step_number = getattr(step_data, 'step_number', None)
                step_label = f"Step {step_number}" if step_number else f"Step {step_node.node_id}"
                errors.append(
                    f"{step_label} must be reachable from a Start node."
                )

        return errors
