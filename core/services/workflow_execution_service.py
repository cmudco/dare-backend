"""
Workflow execution service using modular node handlers.

This service orchestrates workflow execution by delegating to specialized node handlers,
providing a clean, extensible architecture for different node types.
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
    """

    def __init__(self):
        """Initialize the execution service."""
        pass

    async def execute_workflow(self, workflow_run: WorkflowRun) -> Dict[str, Any]:
        """
        Execute a complete workflow using node handlers.

        Args:
            workflow_run: The workflow run to execute

        Returns:
            Dict containing execution results and statistics
        """
        try:
            print(f"🚀 STARTING WORKFLOW EXECUTION - WorkflowRun ID: {workflow_run.id}")

            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            print(f"📋 Workflow loaded - ID: {workflow.id}")

            context = WorkflowExecutionContext(
                workflow_run=workflow_run,
                workflow=workflow,
                node_results={}
            )

            # Get all workflow nodes ordered by step number for step nodes
            nodes = await self._get_ordered_workflow_nodes(workflow)
            print(f"🔍 Found {len(nodes)} nodes to process:")
            for node in nodes:
                print(f"   - {node.type} node: {node.id}")

            if not nodes:
                print("❌ ERROR: No nodes found in workflow")
                return {
                    'success': False,
                    'error': 'No nodes found in workflow',
                    'results': {}
                }

            # Execute nodes with conditional routing logic
            print(f"\n⚡ STARTING NODE EXECUTION...")
            failed_count = 0
            executed_nodes = set()  # Track which nodes have been executed
            skipped_nodes = set()   # Track which nodes were skipped due to routing

            for i, node in enumerate(nodes, 1):
                print(f"\n🔄 PROCESSING NODE {i}/{len(nodes)}: {node.type} - {node.id}")

                # Check if this node should be executed based on routing decisions
                should_execute = await self._should_execute_node(node, context, workflow)

                if not should_execute:
                    print(f"⏭️  SKIPPING node {node.id} due to routing decision")
                    skipped_nodes.add(node.id)
                    # Add skipped node result to context
                    context.node_results[node.id] = NodeExecutionResult(
                        success=True,  # Consider skipped nodes as successful
                        output=None,
                        metadata={'skipped': True, 'reason': 'routing_decision'}
                    )

                    # Update database records for skipped nodes
                    if node.type == 'step':
                        await self._update_step_status_to_skipped(workflow_run, node)
                    elif node.type == 'chatOutput':
                        await self._clear_output_node_data(node)

                    continue

                print(f"✅ EXECUTING node {node.id}")
                executed_nodes.add(node.id)
                result = await self._execute_node(node, context)
                context.node_results[node.id] = result

                if not result.success:
                    failed_count += 1
                    print(f"❌ NODE FAILED: {node.id} - {result.error}")
                else:
                    print(f"✅ NODE COMPLETED: {node.id}")
                    if result.output:
                        output_preview = result.output[:100] + "..." if len(result.output) > 100 else result.output
                        print(f"📤 OUTPUT: {output_preview}")
                        context.current_context = result.output

                    # Special handling for aggregator results
                    if result.metadata and 'routing_decision' in result.metadata:
                        routing_decision = result.metadata['routing_decision']
                        print(f"🎯 ROUTING DECISION: '{routing_decision}'")

            print(f"\n📊 EXECUTION SUMMARY:")
            print(f"   ✅ Executed: {len(executed_nodes)} nodes")
            print(f"   ⏭️  Skipped: {len(skipped_nodes)} nodes")
            print(f"   ❌ Failed: {failed_count} nodes")

            # Update workflow run status
            final_status = 'completed' if failed_count == 0 else 'failed'
            print(f"\n🏁 WORKFLOW COMPLETION:")
            print(f"   Status: {final_status.upper()}")
            await self._update_workflow_run_status(workflow_run, final_status)

            results_dict = {
                'success': failed_count == 0,
                'total_nodes': len(nodes),
                'executed_nodes': len(executed_nodes),
                'skipped_nodes': len(skipped_nodes),
                'failed_nodes': failed_count,
                'results': {node_id: {
                    'success': result.success,
                    'output': result.output,
                    'error': result.error,
                    'token_usage': result.token_usage,
                    'skipped': result.metadata.get('skipped', False) if result.metadata else False
                } for node_id, result in context.node_results.items()}
            }

            print(f"✅ WORKFLOW EXECUTION COMPLETED!")
            return results_dict

        except Exception as e:
            print(f"💥 WORKFLOW EXECUTION FAILED: {str(e)}")
            logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'results': {}
            }

    async def _get_ordered_workflow_nodes(self, workflow: Workflow) -> List[ExecutionNode]:
        """
        Get workflow nodes in execution order.

        Returns nodes ordered by: start node first, then step nodes by step_number,
        then output nodes, then aggregator nodes.
        """
        db_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()

        execution_nodes = []
        for db_node in db_nodes:
            step_number = None
            # Skip data_object access for now to avoid async issues
            # TODO: Implement proper async data_object access if step ordering is needed

            exec_node = ExecutionNode(
                id=db_node.node_id,
                type=db_node.node_type,
                step_number=step_number,
                db_node=db_node
            )
            execution_nodes.append(exec_node)

        # Sort nodes based on dependencies to ensure proper execution order
        return await self._sort_nodes_by_dependencies(execution_nodes, workflow)

    async def _sort_nodes_by_dependencies(self, execution_nodes: List[ExecutionNode], workflow: Workflow) -> List[ExecutionNode]:
        """
        Sort nodes based on their dependencies to ensure proper execution order.
        Aggregators must run before nodes that depend on their routing decisions.
        """
        print(f"🔄 SORTING NODES BY DEPENDENCIES...")

        # Get all edges to understand dependencies
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Build dependency map: node_id -> set of nodes it depends on
        dependencies = {node.id: set() for node in execution_nodes}

        for edge in edges:
            dependencies[edge.target].add(edge.source)

        print(f"📍 DEPENDENCY MAP:")
        for node_id, deps in dependencies.items():
            print(f"   {node_id} depends on: {deps}")

        # Topological sort with special handling for aggregator dependencies
        sorted_nodes = []
        remaining_nodes = execution_nodes.copy()

        while remaining_nodes:
            # Find nodes with no unmet dependencies
            ready_nodes = []

            for node in remaining_nodes:
                deps = dependencies[node.id]
                executed_deps = {n.id for n in sorted_nodes}

                # Check if all dependencies are met
                if deps.issubset(executed_deps):
                    ready_nodes.append(node)

            if not ready_nodes:
                # Fallback: if no nodes are ready (circular dependency), take start nodes
                print(f"⚠️  No ready nodes found, using fallback ordering")
                ready_nodes = [n for n in remaining_nodes if n.type == 'start']
                if not ready_nodes:
                    ready_nodes = [remaining_nodes[0]]  # Emergency fallback

            # Sort ready nodes by priority within the same dependency level
            def priority_sort_key(node):
                type_priority = {
                    'start': 0,
                    'step': 1,
                    'chatOutput': 2,
                    'aggregator': 1.5  # Between step and chatOutput
                }.get(node.type, 999)
                return (type_priority, node.step_number or 0)

            ready_nodes.sort(key=priority_sort_key)

            # Add the first ready node to execution order
            next_node = ready_nodes[0]
            sorted_nodes.append(next_node)
            remaining_nodes.remove(next_node)

            print(f"   ➡️  Added to execution order: {next_node.type} - {next_node.id}")

        print(f"✅ FINAL EXECUTION ORDER:")
        for i, node in enumerate(sorted_nodes, 1):
            print(f"   {i}. {node.type} - {node.id}")

        return sorted_nodes

    async def _execute_node(self, node: ExecutionNode, context: WorkflowExecutionContext) -> NodeExecutionResult:
        """
        Execute a single node using the appropriate handler.

        Args:
            node: The node to execute
            context: Execution context

        Returns:
            NodeExecutionResult with execution outcome
        """
        logger.info(f"Executing {node.type} node: {node.id}")

        # Create node execution context for handler
        node_context = NodeExecutionContext(
            workflow_run=context.workflow_run,
            previous_results={
                node_id: {
                    'output': result.output,
                    'success': result.success,
                    'metadata': result.metadata
                } for node_id, result in context.node_results.items()
            },
            current_input=context.current_context
        )

        # Execute using handler registry
        result = await node_handler_registry.execute_node(node, node_context)

        logger.info(f"Node {node.id} execution {'succeeded' if result.success else 'failed'}")
        return result

    async def _should_execute_node(self, node: ExecutionNode, context: WorkflowExecutionContext, workflow: Workflow) -> bool:
        """
        Determine if a node should be executed based on aggregator routing decisions.

        Args:
            node: The node to check
            context: Execution context with previous results
            workflow: The workflow being executed

        Returns:
            bool: True if node should be executed, False if it should be skipped
        """
        print(f"🔍 ROUTING CHECK for node {node.id} (type: {node.type})")

        # Always execute start nodes
        if node.type == 'start':
            print(f"   ✅ Start node - always execute")
            return True

        # Check if this node is connected from an aggregator via conditional routing
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        print(f"   🔗 Total edges in workflow: {len(edges)}")

        # Find edges that target this node
        incoming_edges = [edge for edge in edges if edge.target == node.id]
        print(f"   📥 Incoming edges to {node.id}: {len(incoming_edges)}")

        for i, edge in enumerate(incoming_edges, 1):
            source_node_id = edge.source
            print(f"   📍 Edge {i}: from {source_node_id} → {node.id} (handle: '{edge.source_handle}')")

            # Check if the source node has been processed (executed or skipped)
            if source_node_id in context.node_results:
                source_result = context.node_results[source_node_id]
                print(f"   🎯 Source node {source_node_id} has been executed")

                # If source node was skipped, also skip this node
                if (hasattr(source_result, 'metadata') and source_result.metadata and
                    source_result.metadata.get('skipped')):
                    print(f"   ⏭️  Source node {source_node_id} was SKIPPED - skipping this node too")
                    return False

                # Check if the source node is an aggregator type
                source_nodes = [n for n in await database_sync_to_async(lambda: list(workflow.nodes.all()))() if n.node_id == source_node_id]
                if source_nodes and source_nodes[0].node_type == 'aggregator':
                    print(f"   🔄 Source node {source_node_id} is an AGGREGATOR")

                    # If source is an aggregator with routing decision
                    if (hasattr(source_result, 'metadata') and
                        source_result.metadata and
                        'routing_decision' in source_result.metadata):

                        routing_decision = source_result.metadata['routing_decision']
                        edge_handle = edge.source_handle

                        print(f"   🎯 ROUTING COMPARISON:")
                        print(f"      Decision: '{routing_decision}'")
                        print(f"      Handle:   '{edge_handle}'")

                        # Check if routing decision matches the edge handle
                        # Handle formats: 'output-good', 'output-bad', 'output-average' or 'true', 'false'
                        is_match = False
                        if edge_handle:
                            edge_handle_lower = edge_handle.lower()
                            if edge_handle_lower.startswith('output-'):
                                # For quantitative decisions: extract the decision part after 'output-'
                                expected_decision = edge_handle_lower[7:]  # Remove 'output-' prefix
                                is_match = (routing_decision == expected_decision)
                            else:
                                # For qualitative decisions: direct match
                                is_match = (routing_decision == edge_handle_lower)

                        print(f"      Match:    {is_match}")

                        # Only execute if the routing decision matches the edge handle
                        if not is_match:
                            print(f"   ❌ SKIP: Decision '{routing_decision}' != Handle '{edge_handle}'")
                            return False
                        else:
                            print(f"   ✅ EXECUTE: Decision '{routing_decision}' matches Handle '{edge_handle}'")
                            return True
                    else:
                        print(f"   ⚠️  Aggregator {source_node_id} has no routing decision yet")
                else:
                    print(f"   📝 Source node {source_node_id} is not an aggregator (type: {source_nodes[0].node_type if source_nodes else 'unknown'})")
            else:
                print(f"   ⏳ Source node {source_node_id} not executed yet")

        # If no conditional routing applies, execute the node
        print(f"   ✅ No conditional routing - execute normally")
        return True

    @database_sync_to_async
    def _update_workflow_run_status(self, workflow_run: WorkflowRun, status: str):
        """Update the workflow run status and end time if completed."""
        if status in ['completed', 'failed']:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])

    @database_sync_to_async
    def _update_step_status_to_skipped(self, workflow_run: WorkflowRun, node: ExecutionNode):
        """Update WorkflowRunStep status to skipped for a step node."""
        try:
            from workflows.constants import WorkflowRunStepStatus
            step = WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                step_node=node.db_node
            ).first()

            if step:
                step.status = WorkflowRunStepStatus.SKIPPED
                step.save(update_fields=['status'])
                print(f"   📝 Updated WorkflowRunStep {step.id} status to SKIPPED")
            else:
                print(f"   ⚠️  No WorkflowRunStep found for node {node.id}")
        except Exception as e:
            print(f"   ❌ Error updating WorkflowRunStep status: {e}")

    @database_sync_to_async
    def _clear_output_node_data(self, node: ExecutionNode):
        """Clear ChatOutputNodeData for a skipped output node."""
        try:
            from workflows.models import ChatOutputNodeData
            output_data = node.db_node.data_object

            if output_data and isinstance(output_data, ChatOutputNodeData):
                # Clear the output content to prevent showing stale data
                output_data.status = 'skipped'
                output_data.response = None
                output_data.error = 'Output skipped due to routing decision'
                output_data.save(update_fields=['status', 'response', 'error'])
                print(f"   🧹 Cleared ChatOutputNodeData for skipped output node {node.id}")
            else:
                print(f"   ⚠️  No ChatOutputNodeData found for output node {node.id}")
        except Exception as e:
            print(f"   ❌ Error clearing output node data: {e}")


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