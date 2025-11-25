"""
Context building utility for workflow execution.

This module provides utilities for building and managing workflow execution context,
including result rebuilding for workflow resumption.
"""
from typing import Dict, List
from channels.db import database_sync_to_async

from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.base import NodeExecutionResult


class WorkflowContextBuilder:
    """
    Utility for building and managing workflow execution context.

    Handles:
    - Rebuilding node results from completed steps
    - Managing execution state for resumption
    - Context preparation for node execution
    """

    @staticmethod
    async def rebuild_node_results_from_steps(
        workflow_run_steps: List
    ) -> Dict[str, NodeExecutionResult]:
        """
        Rebuild node_results dictionary from WorkflowRunStep records.

        Used for workflow resumption after human validation to reconstruct
        the execution state from previously completed steps.

        Args:
            workflow_run_steps: List of WorkflowRunStep instances

        Returns:
            Dictionary mapping node_id to NodeExecutionResult
        """
        node_results = {}

        for step in workflow_run_steps:
            step_node = await database_sync_to_async(lambda: step.step_node)()
            if not step_node:
                continue

            node_id = await database_sync_to_async(lambda: step_node.node_id)()
            node_type = await database_sync_to_async(lambda: step_node.node_type)()

            # Add to context based on status
            if step.status == WorkflowRunStepStatus.COMPLETED:
                # Start with the step's actual metadata from the database
                metadata = dict(step.metadata) if step.metadata else {}

                # Add routing-specific metadata for conditional nodes (legacy support)
                if node_type == 'conditional':
                    metadata['is_human_validated'] = True
                    metadata['routing_decision'] = step.response

                # For structuredOutput nodes, selected_route should already be in step.metadata
                # but if it's not, use the response as fallback
                if node_type == 'structuredOutput' and 'selected_route' not in metadata:
                    metadata['selected_route'] = step.response

                node_results[node_id] = NodeExecutionResult(
                    success=True,
                    output=step.response,
                    metadata=metadata
                )
            elif step.status == WorkflowRunStepStatus.SKIPPED:
                node_results[node_id] = NodeExecutionResult(
                    success=True,
                    output=None,
                    metadata={'skipped': True}
                )

        return node_results

    @staticmethod
    def extract_executed_and_skipped_nodes(
        node_results: Dict[str, NodeExecutionResult]
    ) -> tuple[set, set]:
        """
        Extract executed and skipped node IDs from node results.

        Args:
            node_results: Dictionary of node execution results

        Returns:
            Tuple of (executed_nodes, skipped_nodes) sets
        """
        executed_nodes = set(node_results.keys())
        skipped_nodes = {
            node_id
            for node_id, result in node_results.items()
            if result.metadata and result.metadata.get('skipped')
        }

        return executed_nodes, skipped_nodes

    @staticmethod
    def prepare_node_execution_context(
        node_results: Dict[str, NodeExecutionResult],
        node_types: Dict[str, str] = None
    ) -> Dict[str, Dict]:
        """
        Prepare previous_results dictionary for node execution context.

        Transforms NodeExecutionResult objects into dictionaries suitable
        for passing to node handlers. Includes node_type for chain detection.

        Args:
            node_results: Dictionary of node execution results
            node_types: Optional dictionary mapping node_id to node_type

        Returns:
            Dictionary formatted for NodeExecutionContext.previous_results
        """
        return {
            node_id: {
                'output': result.output,
                'success': result.success,
                'metadata': result.metadata,
                'node_type': node_types.get(node_id) if node_types else None
            }
            for node_id, result in node_results.items()
        }

    @staticmethod
    def update_conditional_step_with_user_choice(
        existing_metadata: Dict,
        chosen_route: str
    ) -> Dict:
        """
        Update conditional step metadata with user's routing choice.

        NOTE: All keys MUST be snake_case on backend - DRF converts to camelCase for frontend

        Args:
            existing_metadata: Existing step metadata (preserves AI analysis)
            chosen_route: User's chosen route

        Returns:
            Updated metadata dictionary
        """
        metadata = existing_metadata or {}
        metadata['user_choice'] = chosen_route
        metadata['selected_route'] = chosen_route  # Update the selected route to user's choice
        metadata['is_human_validated'] = True
        return metadata

    @staticmethod
    def create_execution_results_dict(
        node_results: Dict[str, NodeExecutionResult]
    ) -> Dict:
        """
        Create results dictionary for API response.

        Args:
            node_results: Dictionary of node execution results

        Returns:
            Dictionary formatted for API response
        """
        return {
            node_id: {
                'success': result.success,
                'output': result.output,
                'error': result.error,
                'token_usage': result.token_usage,
                'skipped': (
                    result.metadata.get('skipped', False)
                    if result.metadata else False
                ),
                'pending_human_validation': (
                    result.metadata.get('pending_human_validation', False)
                    if result.metadata else False
                )
            }
            for node_id, result in node_results.items()
        }
