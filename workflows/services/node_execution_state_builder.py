"""
Node Execution State Builder Service

Transforms list-based workflow execution data (WorkflowRunStep[]) into a graph-based
node-indexed map (nodeStates: {node_id: state}). This eliminates frontend complexity
by resolving all graph relationships at the backend serialization layer.

Key Features:
- O(1) node state access for frontend
- Unified validation context across node types
- Display node resolution via edge traversal
- Consistent data shape across all nodes
"""

import logging
from typing import Dict, Optional, Any

from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.utils import MetadataKey
from workflows.handlers.utils.constants import NodeType
from workflows.models import (
    WorkflowRun,
    WorkflowRunStep,
    WorkflowNode,
    WorkflowEdge,
    StructuredOutputNodeData,
)
from workflows.services.citation_serialization import serialize_step_citations

logger = logging.getLogger(__name__)


class NodeExecutionStateBuilder:
    """
    Builds graph-based execution state map from workflow run data.

    Transforms:
        WorkflowRun.steps[] (list) → nodeStates{} (map keyed by node_id)

    Usage:
        builder = NodeExecutionStateBuilder()
        node_states = builder.build_state(workflow_run)
    """

    # Maximum depth for chained display nodes (prevents infinite loops)
    MAX_CHAIN_DEPTH = 5

    def build_state(self, workflow_run: WorkflowRun) -> Dict[str, Dict[str, Any]]:
        """
        Build complete node execution state map for a workflow run.

        Args:
            workflow_run: WorkflowRun instance with related workflow

        Returns:
            Dictionary mapping node_id → node state:
            {
                "node-id": {
                    "stepId": int | null,
                    "startedAt": str | null,
                    "nodeType": str,
                    "status": str,
                    "response": str | null,
                    "error": str | null,
                    "validationContext": dict | null,
                    "metadata": dict | null,
                    "snippets": list,
                    "webSearchSources": list
                }
            }

        Performance:
            - 3 database queries total (with prefetching)
            - O(n) time complexity where n = number of nodes
            - O(n) space complexity for cached dictionaries
        """
        workflow = workflow_run.workflow

        # Prefetch all data (3 queries total, plus citation prefetches)
        nodes_by_id = {n.node_id: n for n in workflow.nodes.all()}
        steps_by_node = {
            s.step_node.node_id: s
            for s in workflow_run.steps.select_related('step_node').prefetch_related(
                'snippets__file', 'web_search_sources'
            ).all()
        }
        edges_by_target = {e.target: e for e in workflow.edges.all()}

        logger.debug(
            f"Building node states for run {workflow_run.id}: "
            f"{len(nodes_by_id)} nodes, {len(steps_by_node)} steps"
        )

        # Build state for all nodes in the workflow
        node_states = {}
        for node_id, node in nodes_by_id.items():
            if node.node_type in [NodeType.STEP, NodeType.STRUCTURED_OUTPUT, NodeType.FILE]:
                # Execution nodes - have WorkflowRunStep records
                node_states[node_id] = self._build_execution_node_state(
                    node=node,
                    step=steps_by_node.get(node_id),
                )
            elif node.node_type in [NodeType.CHAT_OUTPUT, NodeType.START]:
                # Display nodes - resolve from connected execution nodes
                node_states[node_id] = self._build_display_node_state(
                    node=node,
                    incoming_edge=edges_by_target.get(node_id),
                    steps_by_node=steps_by_node,
                    edges_by_target=edges_by_target,
                )
            elif node.node_type == NodeType.NOTES:
                # Non-executable decorative nodes - skip silently
                continue
            else:
                # Unknown node type - log warning and provide default state
                logger.warning(f"Unknown node type '{node.node_type}' for node {node_id}")
                node_states[node_id] = self._build_default_state(node)

        return node_states

    def _build_execution_node_state(
        self,
        node: WorkflowNode,
        step: Optional[WorkflowRunStep],
    ) -> Dict[str, Any]:
        """
        Build state for execution nodes (step, structuredOutput).

        These nodes have WorkflowRunStep records with execution data.

        Args:
            node: WorkflowNode instance
            step: Corresponding WorkflowRunStep if executed, None otherwise

        Returns:
            Node state dictionary with stepId, status, response, etc.
        """
        if step is None:
            # Node not yet executed in this run
            return {
    
                "stepId": None,
                "startedAt": None,
                "nodeType": node.node_type,
                "status": "pending",
                "response": None,
                "error": None,
                "validationContext": None,
                "snippets": [],
                "webSearchSources": [],
            }

        # Extract validation context if node is pending human input
        validation_context = None
        if step.status == WorkflowRunStepStatus.PENDING_HUMAN_INPUT:
            validation_context = self._normalize_validation_context(step, node)

        # Extract AI metadata for completed routing nodes (so frontend can display AI analysis)
        metadata = None
        if node.node_type == NodeType.STRUCTURED_OUTPUT and step.metadata:
            metadata = {
                "aiRecommendation": step.metadata.get(MetadataKey.AI_RECOMMENDATION),
                "aiAnalysis": step.metadata.get(MetadataKey.ANALYSIS),
                "isHumanValidated": step.metadata.get(MetadataKey.IS_HUMAN_VALIDATED, False),
                "userChoice": step.metadata.get(MetadataKey.USER_CHOICE),
                "selectedRoute": step.metadata.get(MetadataKey.SELECTED_ROUTE),
            }

        # Serialize snippets and web search sources
        snippets_data, web_search_sources_data = serialize_step_citations(step)

        return {

            "stepId": step.id,
            "startedAt": step.started_at,
            "nodeType": node.node_type,
            "status": step.status,
            "response": step.response,
            "error": step.error,
            "validationContext": validation_context,
            "metadata": metadata,  # Include AI analysis for completed steps
            "snippets": snippets_data,
            "webSearchSources": web_search_sources_data,
        }

    def _build_display_node_state(
        self,
        node: WorkflowNode,
        incoming_edge: Optional[WorkflowEdge],
        steps_by_node: Dict[str, WorkflowRunStep],
        edges_by_target: Dict[str, WorkflowEdge],
        chain_depth: int = 0,
    ) -> Dict[str, Any]:
        """
        Build state for display nodes (chatOutput, start).

        Display nodes don't have WorkflowRunStep records. Their state is resolved
        by following incoming edges to find connected execution nodes.

        Args:
            node: WorkflowNode instance (chatOutput or start)
            incoming_edge: Edge connecting to this node, None if no connection
            steps_by_node: Dictionary mapping node_id → WorkflowRunStep
            edges_by_target: Dictionary mapping target node_id → incoming edge
            chain_depth: Current depth in display node chain (prevents infinite loops)

        Returns:
            Node state dictionary resolved from connected execution node
        """
        # Prevent infinite loops in chained display nodes
        if chain_depth >= self.MAX_CHAIN_DEPTH:
            logger.warning(
                f"Max chain depth ({self.MAX_CHAIN_DEPTH}) reached for node {node.node_id}"
            )
            return self._build_error_state(
                node, "Maximum display node chain depth exceeded"
            )

        # Handle nodes with no incoming edge
        if incoming_edge is None:
            return self._build_unconnected_state(node)

        source_node_id = incoming_edge.source

        # Check if source is an execution node with a step
        source_step = steps_by_node.get(source_node_id)
        if source_step is not None:
            # Source is an execution node - copy its state including citations
            snippets_data, web_search_sources_data = serialize_step_citations(source_step)

            return {
    
                "stepId": None,  # Display nodes don't have their own steps
                "startedAt": source_step.started_at,
                "nodeType": node.node_type,
                "status": source_step.status,
                "response": source_step.response,
                "error": source_step.error,
                "validationContext": None,  # Display nodes don't show validation UI
                "snippets": snippets_data,
                "webSearchSources": web_search_sources_data,
            }

        # Source might be another display node - follow the chain
        source_edge = edges_by_target.get(source_node_id)
        if source_edge is not None:
            logger.debug(
                f"Chained display node: {node.node_id} → {source_node_id} "
                f"(depth {chain_depth + 1})"
            )
            # Recursively resolve through display node chain
            # Note: We're creating a minimal node-like dict for recursion
            # In a real scenario, we'd look up the actual WorkflowNode
            return self._build_display_node_state(
                node=node,  # Keep original node for type
                incoming_edge=source_edge,
                steps_by_node=steps_by_node,
                edges_by_target=edges_by_target,
                chain_depth=chain_depth + 1,
            )

        # Source exists but has no step and no incoming edge - not executed yet
        return {

            "stepId": None,
            "startedAt": None,
            "nodeType": node.node_type,
            "status": "pending",
            "response": None,
            "error": None,
            "validationContext": None,
            "snippets": [],
            "webSearchSources": [],
        }

    def _build_unconnected_state(self, node: WorkflowNode) -> Dict[str, Any]:
        """
        Build state for display nodes with no incoming connection.

        Special case for start nodes: they're entry points, not errors.
        For other display nodes: this is likely a configuration issue.
        """
        if node.node_type == NodeType.START:
            # Start nodes are entry points - this is expected
            return {
    
                "stepId": None,
                "startedAt": None,
                "nodeType": NodeType.START,
                "status": "not_executed",
                "response": None,
                "error": None,
                "validationContext": None,
                "snippets": [],
                "webSearchSources": [],
            }
        else:
            # Other display nodes without connections - likely misconfigured
            logger.warning(f"Display node {node.node_id} has no incoming edge")
            return {
    
                "stepId": None,
                "startedAt": None,
                "nodeType": node.node_type,
                "status": "no_source",
                "response": None,
                "error": "No connected execution node",
                "validationContext": None,
                "snippets": [],
                "webSearchSources": [],
            }

    def _build_error_state(self, node: WorkflowNode, error_message: str) -> Dict[str, Any]:
        """Build error state for a node."""
        return {

            "stepId": None,
            "startedAt": None,
            "nodeType": node.node_type,
            "status": "error",
            "response": None,
            "error": error_message,
            "validationContext": None,
            "snippets": [],
            "webSearchSources": [],
        }

    def _build_default_state(self, node: WorkflowNode) -> Dict[str, Any]:
        """Build default state for unknown node types."""
        return {

            "stepId": None,
            "startedAt": None,
            "nodeType": node.node_type,
            "status": "unknown",
            "response": None,
            "error": f"Unknown node type: {node.node_type}",
            "validationContext": None,
            "snippets": [],
            "webSearchSources": [],
        }

    def _normalize_validation_context(
        self,
        step: WorkflowRunStep,
        node: WorkflowNode,
    ) -> Optional[Dict[str, Any]]:
        """
        Normalize validation context from StructuredOutputNode.

        Uses standardized MetadataKey constants for consistency:
        - MetadataKey.AI_RECOMMENDATION: AI's suggested route
        - MetadataKey.ANALYSIS: AI's reasoning/explanation
        - MetadataKey.AVAILABLE_ROUTES: Full route objects [{name, description}]

        Args:
            step: WorkflowRunStep with PENDING_HUMAN_INPUT status
            node: WorkflowNode (should be structuredOutput type)

        Returns:
            Normalized validation context:
            {
                "availableRoutes": [{"name": str, "description": str}, ...],
                "customPrompt": str,
                "aiRecommendation": str | null,
                "aiAnalysis": str | null,
                "label": str | null
            }

            Returns None if not a validation-capable node or missing data.
        """
        if step.status != WorkflowRunStepStatus.PENDING_HUMAN_INPUT:
            return None

        metadata = step.metadata or {}
        node_data = node.data_object

        # Only structuredOutput nodes have validation
        if not isinstance(node_data, StructuredOutputNodeData):
            return None

        # Extract available routes from metadata (full route objects)
        # Fallback to node configuration if not in metadata
        available_routes = metadata.get(MetadataKey.AVAILABLE_ROUTES) or []
        if not available_routes and hasattr(node_data, 'get_routes'):
            available_routes = node_data.get_routes()

        # Extract AI analysis using standardized key
        ai_analysis = metadata.get(MetadataKey.ANALYSIS)

        # Extract custom prompt from node configuration
        custom_prompt = ""
        if hasattr(node_data, 'prompt') and node_data.prompt:
            custom_prompt = node_data.prompt.content

        # Extract AI recommendation using standardized key
        ai_recommendation = metadata.get(MetadataKey.AI_RECOMMENDATION)

        return {
            "availableRoutes": available_routes,
            "customPrompt": custom_prompt,
            "aiRecommendation": ai_recommendation,
            "aiAnalysis": ai_analysis,
            "label": node.label,
        }
