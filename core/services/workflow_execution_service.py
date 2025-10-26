"""
Workflow execution service using modular node handlers.

This service orchestrates workflow execution by delegating to specialized node handlers,
providing a clean, extensible architecture for different node types.

Refactored to use utility modules for better maintainability and code organization.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from django.utils import timezone
from channels.db import database_sync_to_async

from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep, WorkflowNode
)
from workflows.constants import WorkflowRunStepStatus
from workflows.node_handlers import (
    node_handler_registry, NodeExecutionContext, NodeExecutionResult, ExecutionNode
)
from core.services.workflow_utils import (
    DependencySorter, RoutingEvaluator, WorkflowContextBuilder
)


logger = logging.getLogger(__name__)


@dataclass
class WorkflowExecutionContext:
    """Context passed through workflow execution."""
    workflow_run: WorkflowRun
    workflow: Workflow
    node_results: Dict[str, NodeExecutionResult]
    current_context: Optional[str] = None  # Response from previous step


class WorkflowExecutionService:
    """
    Service for executing workflows using modular node handlers.

    Orchestrates workflow execution by delegating to specialized handlers
    for different node types, providing clean separation of concerns.

    Enhanced with utility modules for dependency sorting, routing evaluation,
    and context building following best practices from handler patterns.
    """

    def __init__(self):
        """Initialize the execution service."""
        pass

    async def resume_workflow_after_human_validation(
        self,
        workflow_run: WorkflowRun,
        node_id: str,
        chosen_route: str
    ) -> Dict[str, Any]:
        """
        Resume workflow execution after human validation choice.

        Args:
            workflow_run: The workflow run to resume
            node_id: The conditional node ID that was waiting for input
            chosen_route: The route name chosen by the user

        Returns:
            Dict containing execution results
        """
        try:
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()

            # Get the conditional node's step
            conditional_step = await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run,
                    step_node__node_id=node_id,
                    status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
                ).first()
            )()

            if not conditional_step:
                return {
                    'success': False,
                    'error': 'No pending human validation found for this node',
                    'results': {}
                }

            # Update the conditional step with user's choice
            await self._update_conditional_step_with_user_choice(
                conditional_step, chosen_route
            )

            logger.info(
                f"Resuming workflow {workflow_run.id} from node {node_id} "
                f"with route: {chosen_route}"
            )

            # Build context with results from steps executed so far
            context = await self._rebuild_execution_context(workflow_run, workflow)

            # Get all workflow nodes and continue from where we left off
            nodes = await self._get_ordered_workflow_nodes(workflow)

            # Find the index of the conditional node
            conditional_node_idx = next(
                (i for i, n in enumerate(nodes) if n.id == node_id), -1
            )

            if conditional_node_idx == -1:
                return {
                    'success': False,
                    'error': f'Could not find node {node_id} in workflow',
                    'results': {}
                }

            # Continue execution from the next node
            execution_stats = await self._execute_nodes_from_index(
                nodes, conditional_node_idx + 1, context, workflow
            )

            # Update workflow run status
            if execution_stats['pending_human_input']:
                final_status = 'pending_human_input'
            else:
                final_status = (
                    'completed' if execution_stats['failed_count'] == 0 else 'failed'
                )
                await self._update_workflow_run_status(workflow_run, final_status)

            # Build results dictionary
            results_dict = WorkflowContextBuilder.create_execution_results_dict(
                context.node_results
            )

            return {
                'success': execution_stats['failed_count'] == 0 and not execution_stats['pending_human_input'],
                'pending_human_input': execution_stats['pending_human_input'],
                'total_nodes': len(nodes),
                'executed_nodes': len(execution_stats['executed_nodes']),
                'skipped_nodes': len(execution_stats['skipped_nodes']),
                'failed_nodes': execution_stats['failed_count'],
                'results': results_dict
            }

        except Exception as e:
            logger.error(f"Workflow resume failed: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'results': {}
            }

    async def execute_workflow(self, workflow_run: WorkflowRun) -> Dict[str, Any]:
        """
        Execute a complete workflow using node handlers.

        Args:
            workflow_run: The workflow run to execute

        Returns:
            Dict containing execution results and statistics
        """
        try:
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            context = WorkflowExecutionContext(
                workflow_run=workflow_run,
                workflow=workflow,
                node_results={}
            )

            # Get all workflow nodes ordered by dependencies
            nodes = await self._get_ordered_workflow_nodes(workflow)

            if not nodes:
                return {
                    'success': False,
                    'error': 'No nodes found in workflow',
                    'results': {}
                }

            # Execute nodes with conditional routing logic
            execution_stats = await self._execute_nodes_from_index(
                nodes, 0, context, workflow
            )

            # Update workflow run status
            if execution_stats['pending_human_input']:
                final_status = 'pending_human_input'
            else:
                final_status = (
                    'completed' if execution_stats['failed_count'] == 0 else 'failed'
                )
                await self._update_workflow_run_status(workflow_run, final_status)

            # Build results dictionary
            results_dict = WorkflowContextBuilder.create_execution_results_dict(
                context.node_results
            )

            return {
                'success': execution_stats['failed_count'] == 0 and not execution_stats['pending_human_input'],
                'pending_human_input': execution_stats['pending_human_input'],
                'total_nodes': len(nodes),
                'executed_nodes': len(execution_stats['executed_nodes']),
                'skipped_nodes': len(execution_stats['skipped_nodes']),
                'failed_nodes': execution_stats['failed_count'],
                'results': results_dict
            }

        except Exception as e:
            logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'results': {}
            }

    async def _get_ordered_workflow_nodes(self, workflow: Workflow) -> List[ExecutionNode]:
        """
        Get workflow nodes in execution order.

        Returns nodes ordered by dependencies using topological sort.
        """
        db_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()

        execution_nodes = []
        for db_node in db_nodes:
            exec_node = ExecutionNode(
                id=db_node.node_id,
                type=db_node.node_type,
                step_number=None,
                db_node=db_node
            )
            execution_nodes.append(exec_node)

        # Sort nodes based on dependencies using utility
        return await self._sort_nodes_by_dependencies(execution_nodes, workflow)

    async def _sort_nodes_by_dependencies(
        self,
        execution_nodes: List[ExecutionNode],
        workflow: Workflow
    ) -> List[ExecutionNode]:
        """
        Sort nodes based on their dependencies to ensure proper execution order.

        Uses DependencySorter utility for topological sorting with special handling
        for conditional nodes and multi-input nodes.
        """
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        return DependencySorter.sort_nodes_by_dependencies(execution_nodes, edges)

    async def _execute_nodes_from_index(
        self,
        nodes: List[ExecutionNode],
        start_index: int,
        context: WorkflowExecutionContext,
        workflow: Workflow
    ) -> Dict[str, Any]:
        """
        Execute nodes starting from a given index.

        Args:
            nodes: List of all nodes in execution order
            start_index: Index to start execution from
            context: Execution context
            workflow: Workflow being executed

        Returns:
            Dict with execution statistics
        """
        failed_count = 0
        executed_nodes = set(context.node_results.keys())
        skipped_nodes = set()
        pending_human_input = False

        for node in nodes[start_index:]:
            # Check if this node should be executed based on routing decisions
            should_execute = await self._should_execute_node(node, context, workflow)

            if not should_execute:
                skipped_nodes.add(node.id)
                context.node_results[node.id] = NodeExecutionResult(
                    success=True,
                    output=None,
                    metadata={'skipped': True, 'reason': 'routing_decision'}
                )

                # Update database records for skipped nodes
                if node.type == 'step':
                    await self._update_step_status_to_skipped(
                        context.workflow_run, node
                    )
                elif node.type == 'chatOutput':
                    await self._clear_output_node_data(node)

                continue

            executed_nodes.add(node.id)
            result = await self._execute_node(node, context)
            context.node_results[node.id] = result

            # Check if this node is waiting for human input
            if (not result.success and
                result.error == "PENDING_HUMAN_INPUT" and
                result.metadata and
                result.metadata.get('pending_human_validation')):

                pending_human_input = True
                logger.info(
                    f"Workflow {context.workflow_run.id} paused at node {node.id} "
                    "- waiting for human validation"
                )
                break

            if not result.success:
                failed_count += 1
            else:
                context.current_context = result.output

        return {
            'failed_count': failed_count,
            'executed_nodes': executed_nodes,
            'skipped_nodes': skipped_nodes,
            'pending_human_input': pending_human_input
        }

    async def _execute_node(
        self,
        node: ExecutionNode,
        context: WorkflowExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a single node using the appropriate handler.

        Args:
            node: The node to execute
            context: Execution context

        Returns:
            NodeExecutionResult with execution outcome
        """
        # Create node execution context for handler
        node_context = NodeExecutionContext(
            workflow_run=context.workflow_run,
            previous_results=WorkflowContextBuilder.prepare_node_execution_context(
                context.node_results
            ),
            current_input=context.current_context
        )

        # Execute using handler registry
        result = await node_handler_registry.execute_node(node, node_context)
        return result

    async def _should_execute_node(
        self,
        node: ExecutionNode,
        context: WorkflowExecutionContext,
        workflow: Workflow
    ) -> bool:
        """
        Determine if a node should be executed based on conditional routing decisions.

        Uses RoutingEvaluator utility for routing constraint evaluation.

        Args:
            node: The node to check
            context: Execution context with previous results
            workflow: The workflow being executed

        Returns:
            True if node should be executed, False if it should be skipped
        """
        # Get all edges and nodes for routing evaluation
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()

        # Use utility for routing evaluation
        return RoutingEvaluator.should_execute_node(
            node, context.node_results, nodes, edges
        )

    async def _rebuild_execution_context(
        self,
        workflow_run: WorkflowRun,
        workflow: Workflow
    ) -> WorkflowExecutionContext:
        """
        Rebuild execution context from completed workflow run steps.

        Used for workflow resumption to restore state from database.

        Args:
            workflow_run: The workflow run to rebuild context for
            workflow: The workflow being executed

        Returns:
            WorkflowExecutionContext with restored state
        """
        # Get all completed steps
        all_steps = await database_sync_to_async(
            lambda: list(WorkflowRunStep.objects.filter(workflow_run=workflow_run))
        )()

        # Rebuild node results using utility
        node_results = await WorkflowContextBuilder.rebuild_node_results_from_steps(
            all_steps
        )

        return WorkflowExecutionContext(
            workflow_run=workflow_run,
            workflow=workflow,
            node_results=node_results
        )

    async def _update_conditional_step_with_user_choice(
        self,
        conditional_step: WorkflowRunStep,
        chosen_route: str
    ):
        """
        Update conditional step with user's routing choice.

        Args:
            conditional_step: The conditional step to update
            chosen_route: User's chosen route
        """
        @database_sync_to_async
        def update_step():
            step = WorkflowRunStep.objects.get(id=conditional_step.id)
            existing_metadata = step.metadata or {}

            # Update metadata using utility
            updated_metadata = WorkflowContextBuilder.update_conditional_step_with_user_choice(
                existing_metadata, chosen_route
            )

            WorkflowRunStep.objects.filter(id=conditional_step.id).update(
                status=WorkflowRunStepStatus.COMPLETED,
                response=chosen_route,
                metadata=updated_metadata
            )

        await update_step()

    @database_sync_to_async
    def _update_workflow_run_status(self, workflow_run: WorkflowRun, status: str):
        """Update the workflow run status and end time if completed."""
        if status in ['completed', 'failed']:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])

    @database_sync_to_async
    def _update_step_status_to_skipped(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode
    ):
        """Update WorkflowRunStep status to skipped for a step node."""
        try:
            step = WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                step_node=node.db_node
            ).first()

            if step:
                step.status = WorkflowRunStepStatus.SKIPPED
                step.response = 'Output skipped due to routing decision'
                step.save(update_fields=['status', 'response'])
        except Exception as e:
            logger.error(f"Error updating WorkflowRunStep status: {e}")

    @database_sync_to_async
    def _clear_output_node_data(self, node: ExecutionNode):
        """Clear ChatOutputNodeData for a skipped output node."""
        try:
            from workflows.models import ChatOutputNodeData
            output_data = node.db_node.data_object

            if output_data and isinstance(output_data, ChatOutputNodeData):
                output_data.status = 'skipped'
                output_data.response = 'Output skipped due to routing decision'
                output_data.error = ''
                output_data.save(update_fields=['status', 'response', 'error'])
        except Exception as e:
            logger.error(f"Error clearing output node data: {e}")


# Convenience function for external use
async def execute_workflow_graph(workflow_run: WorkflowRun) -> Dict[str, Any]:
    """
    Execute a workflow using the node handler execution service.

    Args:
        workflow_run: The workflow run to execute

    Returns:
        Dict containing execution results
    """
    service = WorkflowExecutionService()
    return await service.execute_workflow(workflow_run)
