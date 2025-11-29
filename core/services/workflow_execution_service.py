"""
Workflow execution service using modular node handlers.

This service orchestrates workflow execution by delegating to specialized node handlers,
providing a clean, extensible architecture for different node types.

Refactored to use utility modules for better maintainability and code organization.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from channels.db import database_sync_to_async
from django.utils import timezone

from core.services.workflow_utils import (
    DependencySorter, RoutingEvaluator, WorkflowContextBuilder
)
from workflows.constants import WorkflowRunStepStatus
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep, WorkflowNode, ChatOutputNodeData
)
from workflows.node_handlers import (
    node_handler_registry, NodeExecutionContext, NodeExecutionResult, ExecutionNode
)


logger = logging.getLogger(__name__)


@dataclass
class WorkflowExecutionContext:
    """Context passed through workflow execution."""
    workflow_run: WorkflowRun
    workflow: Workflow
    node_results: Dict[str, NodeExecutionResult]
    # REMOVED: current_context (use edge-based data flow instead)


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
            # Check if this step was already completed (for partial runs being completed)
            already_completed = await self._is_step_already_completed(context.workflow_run, node)

            if already_completed:
                # Load the existing result from the database
                existing_result = await self._get_existing_step_result(context.workflow_run, node)
                context.node_results[node.id] = existing_result
                executed_nodes.add(node.id)
                logger.info(
                    f"Skipping already-completed step {node.id} in workflow run {context.workflow_run.id}"
                )
                continue

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
                elif node.type in ('conditional', 'structuredOutput'):
                    # Routing nodes can also be skipped if all predecessors are skipped
                    await self._update_step_status_to_skipped(
                        context.workflow_run, node
                    )

                continue

            executed_nodes.add(node.id)
            result = await self._execute_node(node, context, workflow)
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
            # REMOVED: context.current_context assignment (use edge-based data flow)

        return {
            'failed_count': failed_count,
            'executed_nodes': executed_nodes,
            'skipped_nodes': skipped_nodes,
            'pending_human_input': pending_human_input
        }

    async def _execute_node(
        self,
        node: ExecutionNode,
        context: WorkflowExecutionContext,
        workflow: Workflow = None
    ) -> NodeExecutionResult:
        """
        Execute a single node using the appropriate handler.

        Args:
            node: The node to execute
            context: Execution context
            workflow: The workflow being executed (optional, for parallel mode filtering)

        Returns:
            NodeExecutionResult with execution outcome
        """
        # For parallel workflows, only pass results from dependency nodes
        filtered_results = await self._get_node_dependency_results(
            node, context, workflow
        )

        # Build node_types map for chain detection
        node_types = await self._build_node_types_map(filtered_results, context)

        # Create node execution context for handler
        node_context = NodeExecutionContext(
            workflow_run=context.workflow_run,
            previous_results=WorkflowContextBuilder.prepare_node_execution_context(
                filtered_results,
                node_types=node_types
            )
            # REMOVED: current_input parameter (use edge-based data flow)
        )

        # Execute using handler registry
        result = await node_handler_registry.execute_node(node, node_context)
        return result

    async def _build_node_types_map(
        self,
        filtered_results: Dict[str, NodeExecutionResult],
        context: WorkflowExecutionContext
    ) -> Dict[str, str]:
        """
        Build a map of node_id to node_type for chain detection.

        Args:
            filtered_results: Results from dependency nodes
            context: Execution context

        Returns:
            Dictionary mapping node_id to node_type
        """
        node_types = {}

        # Get workflow to access nodes
        workflow = await database_sync_to_async(
            lambda: context.workflow_run.workflow
        )()

        # Get all nodes from workflow
        all_nodes = await database_sync_to_async(
            lambda: list(workflow.nodes.all())
        )()

        # Build map for filtered results
        for node_id in filtered_results.keys():
            for db_node in all_nodes:
                if db_node.node_id == node_id:
                    node_types[node_id] = db_node.node_type
                    break

        return node_types

    async def _get_node_dependency_results(
        self,
        node: ExecutionNode,
        context: WorkflowExecutionContext,
        workflow: Workflow = None
    ) -> Dict[str, NodeExecutionResult]:
        """
        Get only the results from nodes that this node depends on.

        For parallel workflows, this filters out sibling node results
        to ensure true parallelism and independence.

        Args:
            node: The node to get dependencies for
            context: Execution context with all results
            workflow: The workflow being executed

        Returns:
            Dictionary with only relevant node results
        """
        # If no workflow provided, return all results (sequential mode)
        if not workflow:
            return context.node_results

        # Get all edges from workflow
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Find all nodes that this node depends on (incoming edges)
        dependency_node_ids = {
            edge.source for edge in edges if edge.target == node.id
        }

        # Filter results to only include dependency nodes
        filtered_results = {
            node_id: result
            for node_id, result in context.node_results.items()
            if node_id in dependency_node_ids or dependency_node_ids == set()
        }

        return filtered_results

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

    async def execute_single_step(
        self,
        workflow_run: WorkflowRun,
        step_node_id: str
    ) -> Dict[str, Any]:
        """
        Execute a single step node in a workflow.

        Used for manual step-by-step execution. Validates dependencies before execution.

        Args:
            workflow_run: The (partial) workflow run to execute in
            step_node_id: The node_id of the step to execute

        Returns:
            Dict containing:
                - success: bool
                - step_result: NodeExecutionResult or None
                - missing_dependencies: List[str] (node IDs of unexecuted dependencies)
                - error: str or None
        """
        try:
            logger.info(f"Starting single step execution for node {step_node_id} in workflow run {workflow_run.id}")
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()

            # Get the step node
            step_node = await database_sync_to_async(
                lambda: WorkflowNode.objects.filter(
                    workflow=workflow,
                    node_id=step_node_id
                ).first()
            )()

            if not step_node:
                return {
                    'success': False,
                    'step_result': None,
                    'missing_dependencies': [],
                    'error': f'Step node {step_node_id} not found'
                }

            # Check if dependencies are satisfied
            can_execute, missing_deps = await self.can_execute_step(
                workflow_run, step_node_id, workflow
            )

            if not can_execute:
                logger.warning(f"Cannot execute node {step_node_id}: missing dependencies {missing_deps}")
                return {
                    'success': False,
                    'step_result': None,
                    'missing_dependencies': missing_deps,
                    'error': f'Cannot execute step. Missing dependencies: {", ".join(missing_deps)}'
                }

            # Rebuild execution context from already-executed steps
            context = await self._rebuild_execution_context(workflow_run, workflow)

            # Get step number from data object in async-safe way
            step_number = await database_sync_to_async(
                lambda: getattr(step_node.data_object, 'step_number', None)
            )()

            # Create execution node
            execution_node = ExecutionNode(
                id=step_node.node_id,
                type=step_node.node_type,
                step_number=step_number,
                db_node=step_node
            )

            # Get filtered dependency results for this specific node
            dependency_results = await self._get_node_dependency_results(
                execution_node,
                context,
                workflow
            )

            logger.info(
                f"[execute_single_step] Node {step_node_id} - Found {len(dependency_results)} dependency results: "
                f"{list(dependency_results.keys())}"
            )

            # Log each dependency result for debugging
            for dep_node_id, dep_result in dependency_results.items():
                output_preview = (
                    dep_result.output[:100] + "..."
                    if dep_result.output and len(dep_result.output) > 100
                    else dep_result.output
                )
                logger.info(
                    f"[execute_single_step]   Dependency {dep_node_id}: "
                    f"success={dep_result.success}, "
                    f"output_length={len(dep_result.output) if dep_result.output else 0}, "
                    f"preview='{output_preview}'"
                )

            # Build node_types map for chain detection
            node_types = await self._build_node_types_map(dependency_results, context)

            logger.info(
                f"[execute_single_step] Node {step_node_id} - Built node_types map: {node_types}"
            )

            # Prepare context for execution
            prepared_context = WorkflowContextBuilder.prepare_node_execution_context(
                dependency_results,
                node_types=node_types
            )

            logger.info(
                f"[execute_single_step] Node {step_node_id} - Prepared context has {len(prepared_context)} entries: "
                f"{list(prepared_context.keys())}"
            )

            # Execute the node with properly prepared context
            node_context = NodeExecutionContext(
                workflow_run=workflow_run,
                previous_results=prepared_context
            )

            logger.info(f"Executing node {step_node_id} with handler registry")
            result = await node_handler_registry.execute_node(
                execution_node,
                node_context
            )

            # Store result in context
            context.node_results[step_node_id] = result

            if result.success:
                logger.info(f"Successfully executed node {step_node_id}")
            else:
                logger.warning(f"Node {step_node_id} execution failed: {result.error}")

            return {
                'success': result.success,
                'step_result': result,
                'missing_dependencies': [],
                'error': result.error if not result.success else None
            }

        except Exception as e:
            logger.error(f"Error executing single step {step_node_id}: {e}", exc_info=True)
            return {
                'success': False,
                'step_result': None,
                'missing_dependencies': [],
                'error': str(e)
            }

    async def can_execute_step(
        self,
        workflow_run: WorkflowRun,
        step_node_id: str,
        workflow: Workflow
    ) -> tuple[bool, List[str]]:
        """
        Check if a step can be executed based on its dependencies.

        Args:
            workflow_run: The workflow run
            step_node_id: The node_id of the step to check
            workflow: The workflow

        Returns:
            Tuple of (can_execute: bool, missing_dependencies: List[str])
        """
        missing_deps = await self.get_missing_dependencies(
            workflow_run, step_node_id, workflow
        )

        return (len(missing_deps) == 0, missing_deps)

    async def get_missing_dependencies(
        self,
        workflow_run: WorkflowRun,
        step_node_id: str,
        workflow: Workflow
    ) -> List[str]:
        """
        Get list of unexecuted dependency node IDs for a given step.

        Only considers step nodes as dependencies (start nodes are excluded).

        Args:
            workflow_run: The workflow run
            step_node_id: The node_id of the step to check
            workflow: The workflow

        Returns:
            List of node IDs that are dependencies but haven't been executed
        """
        # Get all edges to find dependencies (incoming edges to this node)
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Find incoming edges (edges where this node is the target)
        incoming_edges = [edge for edge in edges if edge.target == step_node_id]

        # Get source node IDs (dependencies)
        dependency_node_ids = [edge.source for edge in incoming_edges]

        if not dependency_node_ids:
            # No dependencies, can execute
            return []

        # Get all nodes to filter out non-step nodes
        all_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        node_types = {node.node_id: node.node_type for node in all_nodes}

        # Filter to only include step nodes (exclude start, conditional, output nodes, etc.)
        step_dependency_node_ids = [
            dep_id for dep_id in dependency_node_ids
            if node_types.get(dep_id) == 'step'
        ]

        if not step_dependency_node_ids:
            # No step dependencies, can execute
            return []

        # Get completed steps for this workflow run
        completed_steps = await database_sync_to_async(
            lambda: list(WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                status__in=[
                    WorkflowRunStepStatus.COMPLETED,
                    WorkflowRunStepStatus.SKIPPED
                ]
            ).values_list('step_node__node_id', flat=True))
        )()

        # Find which dependencies are missing
        missing = [
            dep_id for dep_id in step_dependency_node_ids
            if dep_id not in completed_steps
        ]

        return missing

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

        logger.info(
            f"[_rebuild_execution_context] Rebuilding context for workflow_run {workflow_run.id} "
            f"with {len(all_steps)} WorkflowRunStep records"
        )

        # Rebuild node results using utility
        node_results = await WorkflowContextBuilder.rebuild_node_results_from_steps(
            all_steps
        )

        logger.info(
            f"[_rebuild_execution_context] Rebuilt {len(node_results)} node results from steps: "
            f"{list(node_results.keys())}"
        )

        # Also rebuild chatOutput node results (for aggregator nodes that depend on outputs)
        # Pass step_results so chatOutput nodes can be built from their source steps
        chatoutput_results = await self._rebuild_chatoutput_node_results(workflow, node_results)

        logger.info(
            f"[_rebuild_execution_context] Rebuilt {len(chatoutput_results)} chatOutput node results: "
            f"{list(chatoutput_results.keys())}"
        )

        # Log each chatOutput result for debugging
        for output_node_id, output_result in chatoutput_results.items():
            output_preview = (
                output_result.output[:100] + "..."
                if output_result.output and len(output_result.output) > 100
                else output_result.output
            )
            logger.info(
                f"[_rebuild_execution_context]   ChatOutput {output_node_id}: "
                f"success={output_result.success}, "
                f"output_length={len(output_result.output) if output_result.output else 0}, "
                f"preview='{output_preview}'"
            )

        node_results.update(chatoutput_results)

        logger.info(
            f"[_rebuild_execution_context] Final context has {len(node_results)} total node results: "
            f"{list(node_results.keys())}"
        )

        return WorkflowExecutionContext(
            workflow_run=workflow_run,
            workflow=workflow,
            node_results=node_results
        )

    async def _rebuild_chatoutput_node_results(
        self,
        workflow: Workflow,
        step_results: Dict[str, NodeExecutionResult] = None
    ) -> Dict[str, NodeExecutionResult]:
        """
        Rebuild chatOutput node results from their connected step nodes.

        In partial/manual execution mode, chatOutput nodes may not be executed
        even though their source step nodes are. This function rebuilds output
        node results by finding their source step nodes via edges.

        Args:
            workflow: The workflow containing chatOutput nodes
            step_results: Already-rebuilt results from step nodes (for finding sources)

        Returns:
            Dictionary mapping chatOutput node_id to NodeExecutionResult
        """
        @database_sync_to_async
        def get_chatoutput_nodes():
            # Get all chatOutput nodes
            output_nodes = list(workflow.nodes.filter(node_type='chatOutput'))

            # Get all edges to find source->output connections
            edges = list(workflow.edges.all())

            results = {}

            for output_node in output_nodes:
                # Find the edge pointing TO this output node
                incoming_edges = [e for e in edges if e.target == output_node.node_id]

                if not incoming_edges:
                    continue

                # Get the source node ID (should be a step node)
                source_node_id = incoming_edges[0].source

                # Check if we have a result for the source step node
                if step_results and source_node_id in step_results:
                    source_result = step_results[source_node_id]

                    # Create output node result from source step's output
                    results[output_node.node_id] = NodeExecutionResult(
                        success=source_result.success,
                        output=source_result.output,
                        metadata=source_result.metadata or {}
                    )

                    logger.info(
                        f"[_rebuild_chatoutput_node_results] Created output node {output_node.node_id} "
                        f"from source step {source_node_id}"
                    )

            return results

        return await get_chatoutput_nodes()

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
    def _is_step_already_completed(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode
    ) -> bool:
        """Check if a step was already completed (for partial runs)."""
        if node.type != 'step':
            return False

        try:
            step = WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                step_node=node.db_node,
                status=WorkflowRunStepStatus.COMPLETED
            ).first()
            return step is not None
        except Exception as e:
            logger.error(f"Error checking if step is completed: {e}")
            return False

    @database_sync_to_async
    def _get_existing_step_result(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode
    ) -> NodeExecutionResult:
        """Get the result of an already-completed step."""
        try:
            step = WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                step_node=node.db_node,
                status=WorkflowRunStepStatus.COMPLETED
            ).first()

            if step:
                return NodeExecutionResult(
                    success=True,
                    output=step.response,
                    metadata=step.metadata or {}
                )
            else:
                # Fallback if step not found
                return NodeExecutionResult(
                    success=False,
                    output=None,
                    error="Step not found"
                )
        except Exception as e:
            logger.error(f"Error getting existing step result: {e}")
            return NodeExecutionResult(
                success=False,
                output=None,
                error=str(e)
            )

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
