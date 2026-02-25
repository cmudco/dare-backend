"""
Workflow Execution Service - Simplified (~350 lines)

Core philosophy:
- DB queries are cheap, don't over-cache
- Each step saves to DB immediately  
- Resume = just re-run, completed steps are skipped
- Human validation = pause, emit event, continue when resumed
"""
import logging
from typing import Dict, List, Optional, Any

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.services.websocket_response_service import WebSocketResponseService
from workflows.constants import WorkflowRunStepStatus
from workflows.models import Workflow, WorkflowRun, WorkflowRunStep, WorkflowNode
from workflows.node_handlers import (
    node_handler_registry, NodeExecutionContext, NodeExecutionResult, ExecutionNode
)

logger = logging.getLogger(__name__)


class WorkflowExecutionService:
    """Simple workflow executor - traverse nodes, call handlers, save results."""

    # ==================== Public API ====================

    async def execute_workflow(
        self,
        workflow_run: WorkflowRun = None,
        workflow_run_id: int = None,
        send_callback=None,
        batch_file_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Execute workflow from start or resume from where it left off."""
        try:
            if workflow_run is None:
                workflow_run = await database_sync_to_async(
                    lambda: WorkflowRun.objects.select_related('workflow').get(id=workflow_run_id)
                )()
            
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            effective_batch_file_id = batch_file_id or workflow_run.batch_file_id
            start_connected_step_node_ids = []
            if effective_batch_file_id:
                start_connected_step_node_ids = await self._get_start_connected_step_node_ids(workflow)

            nodes = await self._get_ordered_nodes(workflow)
            
            if not nodes:
                return {'success': False, 'error': 'No nodes found'}

            result = await self._run_nodes(
                workflow_run,
                workflow,
                nodes,
                send_callback,
                batch_file_id=effective_batch_file_id,
                start_connected_step_node_ids=start_connected_step_node_ids
            )

            # Finalize if not waiting for human
            if not result.get('pending_human_input'):
                status = 'completed' if result['success'] else 'failed'
                await self._finalize_run(workflow_run, status, send_callback)

            return result

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            await self._emit(send_callback, WebSocketResponseService.format_workflow_error(
                node_id=None,
                error=str(e),
                workflow_run_id=workflow_run.id if workflow_run else None
            ))
            return {'success': False, 'error': str(e)}

    async def resume_workflow_after_human_validation(
        self,
        workflow_run: WorkflowRun,
        node_id: str,
        chosen_route: str,
        send_callback=None
    ) -> Dict[str, Any]:
        """Resume after human routing decision - just update step and re-run."""
        try:
            # Update pending routing step
            updated = await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run,
                    step_node__node_id=node_id,
                    status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
                ).update(
                    status=WorkflowRunStepStatus.COMPLETED,
                    response=chosen_route,
                    metadata={'selected_route': chosen_route, 'human_validated': True}
                )
            )()

            if not updated:
                return {'success': False, 'error': 'No pending validation found'}

            logger.info(f"Human validation: run={workflow_run.id}, node={node_id}, route={chosen_route}")
            return await self.execute_workflow(workflow_run=workflow_run, send_callback=send_callback)

        except Exception as e:
            logger.error(f"Resume failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def execute_single_step(
        self,
        workflow_run: WorkflowRun,
        step_node_id: str,
        send_callback=None
    ) -> Dict[str, Any]:
        """Execute single step (manual mode)."""
        try:
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            
            step_node = await database_sync_to_async(
                lambda: WorkflowNode.objects.filter(workflow=workflow, node_id=step_node_id).first()
            )()
            if not step_node:
                return {'success': False, 'error': f'Step {step_node_id} not found'}

            # Check dependencies
            missing = await self._get_missing_deps(workflow_run, step_node_id, workflow)
            if missing:
                return {'success': False, 'error': f'Missing deps: {", ".join(missing)}', 'missing_dependencies': missing}

            step_number = await database_sync_to_async(
                lambda: getattr(step_node.data_object, 'step_number', None)
            )()

            exec_node = ExecutionNode(id=step_node.node_id, type=step_node.node_type, 
                                      step_number=step_number, db_node=step_node)
            
            # Load existing results for context
            nodes = [exec_node]  # Just this node for loading
            node_results = await self._load_existing_results(workflow_run, nodes)

            # Execute with is_single_step_execution=True to allow re-running steps
            result = await self._execute_node(
                workflow_run, workflow, exec_node, node_results, send_callback,
                is_single_step_execution=True
            )
            
            await self._emit(send_callback, WebSocketResponseService.format_workflow_execution_complete(
                workflow_run_id=workflow_run.id, status='completed' if result.success else 'failed'
            ))

            return {'success': result.success, 'step_result': result, 'error': result.error}

        except Exception as e:
            logger.error(f"Single step failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    # ==================== Core Loop ====================

    async def _run_nodes(
        self,
        workflow_run,
        workflow,
        nodes,
        send_callback,
        batch_file_id: Optional[int] = None,
        start_connected_step_node_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Main loop: iterate nodes, skip completed, check routing, execute."""
        executed, skipped, failed = [], [], 0
        start_connected_step_node_ids = start_connected_step_node_ids or []
        is_batch_execution = bool(batch_file_id or getattr(workflow_run, 'batch_run_id', None))
        
        # In-memory results dict for routing decisions during this execution
        # This is needed because routing depends on results from nodes executed in THIS run
        node_results: Dict[str, NodeExecutionResult] = {}
        
        # Pre-load results from already-completed steps (for resume scenarios)
        node_results = await self._load_existing_results(workflow_run, nodes)

        for node in nodes:
            # Skip already completed (result already in node_results from DB)
            if node.id in node_results and not node_results[node.id].metadata.get('skipped'):
                executed.append(node.id)
                continue

            # Check routing using in-memory results
            should_exec = await self._should_execute(workflow, node, node_results)
            if not should_exec:
                skipped.append(node.id)
                node_results[node.id] = NodeExecutionResult(
                    success=True, output=None, 
                    metadata={'skipped': True, 'reason': 'routing_decision'}
                )
                await self._mark_skipped(workflow_run, node)
                continue

            # Execute
            result = await self._execute_node(
                workflow_run,
                workflow,
                node,
                node_results,
                send_callback,
                batch_file_id=batch_file_id,
                is_start_connected=node.id in start_connected_step_node_ids
            )
            node_results[node.id] = result
            executed.append(node.id)

            # Human validation pause?
            if self._is_pending_human(result):
                if is_batch_execution:
                    await self._fail_pending_human_step(
                        workflow_run,
                        node,
                        send_callback
                    )
                    failed += 1
                    return {
                        'success': False,
                        'pending_human_input': False,
                        'executed_nodes': len(executed),
                        'skipped_nodes': len(skipped),
                        'failed_nodes': failed
                    }
                await self._emit(send_callback, WebSocketResponseService.format_workflow_validation_required(
                    node_id=node.id,
                    routes=result.metadata.get('available_routes', []),
                    context={'stepNumber': result.metadata.get('step_number'),
                             'customPrompt': result.metadata.get('custom_prompt'),
                             'aiAnalysis': result.metadata.get('ai_analysis')},
                    ai_recommendation=result.metadata.get('ai_recommendation'),
                    workflow_run_id=workflow_run.id
                ))
                logger.info(f"Sent validation_required for node {node.id}")
                return {'success': False, 'pending_human_input': True, 
                        'executed_nodes': len(executed), 'skipped_nodes': len(skipped)}

            if not result.success:
                failed += 1

        return {'success': failed == 0, 'pending_human_input': False,
                'executed_nodes': len(executed), 'skipped_nodes': len(skipped), 'failed_nodes': failed}

    async def _execute_node(
        self, workflow_run, workflow, node, node_results, send_callback,
        is_single_step_execution: bool = False,
        batch_file_id: Optional[int] = None,
        is_start_connected: bool = False
    ) -> NodeExecutionResult:
        """Execute single node via handler registry."""
        # Get dependency results from in-memory dict (not DB)
        previous = await self._get_dep_results_from_memory(workflow, node, node_results)
        context = NodeExecutionContext(
            workflow_run=workflow_run,
            previous_results=previous,
            send_callback=send_callback,
            is_single_step_execution=is_single_step_execution,
            batch_file_id=batch_file_id,
            is_start_connected=is_start_connected
        )
        return await node_handler_registry.execute_node(node, context)

    # ==================== Node Ordering ====================

    async def _get_ordered_nodes(self, workflow) -> List[ExecutionNode]:
        """Get nodes in topological order."""
        db_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()

        # Filter out non-executable node types (e.g. notes are decorative only)
        NON_EXECUTABLE_TYPES = {'notes'}
        nodes = [ExecutionNode(id=n.node_id, type=n.node_type, step_number=None, db_node=n)
                 for n in db_nodes if n.node_type not in NON_EXECUTABLE_TYPES]

        # Kahn's algorithm
        node_map = {n.id: n for n in nodes}
        in_deg = {n.id: 0 for n in nodes}
        for e in edges:
            if e.target in in_deg:
                in_deg[e.target] += 1

        queue = [nid for nid, d in in_deg.items() if d == 0]
        result = []
        type_order = {'start': 0, 'file': 1, 'step': 2, 'structuredOutput': 3, 'chatOutput': 4}

        while queue:
            queue.sort(key=lambda nid: type_order.get(node_map[nid].type, 99))
            nid = queue.pop(0)
            result.append(node_map[nid])
            for e in edges:
                if e.source == nid and e.target in in_deg:
                    in_deg[e.target] -= 1
                    if in_deg[e.target] == 0:
                        queue.append(e.target)

        return result

    # ==================== Batch Helpers ====================

    async def _get_start_connected_step_node_ids(self, workflow) -> List[str]:
        """Get node_ids of step nodes directly connected to root start node."""
        start_node = await database_sync_to_async(workflow._get_root_start_node)()
        if not start_node:
            return []

        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        step_nodes = await database_sync_to_async(
            lambda: {n.node_id for n in workflow.nodes.filter(node_type='step')}
        )()

        return [
            e.target for e in edges
            if e.source == start_node.node_id and e.target in step_nodes
        ]

    # ==================== Routing ====================

    async def _should_execute(self, workflow, node, node_results: Dict[str, NodeExecutionResult]) -> bool:
        """Check if node should execute based on routing decisions using in-memory results."""
        if node.type == 'start':
            return True

        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        db_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        incoming = [e for e in edges if e.target == node.id]
        
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
            source_node = next((n for n in db_nodes if n.node_id == source_node_id), None)
            if not source_node:
                continue

            # Only consider processed sources
            if source_node_id not in node_results:
                continue

            source_result = node_results[source_node_id]
            is_skipped = source_result.metadata and source_result.metadata.get('skipped')

            # Evaluate structuredOutput routing (routing node with output-{route} format)
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

            # Non-routing edge
            if not is_skipped:
                any_non_routing_valid = True

        # Decision: routing edges take precedence
        if has_routing_edge:
            result = any_routing_match
        else:
            result = any_non_routing_valid
        
        logger.info(f"Routing decision for {node.id}: execute={result}, has_routing={has_routing_edge}, match={any_routing_match}, non_routing={any_non_routing_valid}")
        return result

    # ==================== DB Queries ====================

    async def _load_existing_results(self, workflow_run, nodes) -> Dict[str, NodeExecutionResult]:
        """Load results from already-completed steps into memory for routing."""
        results = {}
        
        steps = await database_sync_to_async(
            lambda: list(WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                status__in=[WorkflowRunStepStatus.COMPLETED, WorkflowRunStepStatus.SKIPPED]
            ).select_related('step_node'))
        )()
        
        for step in steps:
            node_id = step.step_node.node_id
            if step.status == WorkflowRunStepStatus.SKIPPED:
                results[node_id] = NodeExecutionResult(
                    success=True, output=None,
                    metadata={'skipped': True}
                )
            else:
                results[node_id] = NodeExecutionResult(
                    success=True,
                    output=step.response,
                    metadata=step.metadata or {}
                )
        
        logger.info(f"Loaded {len(results)} existing results for workflow_run {workflow_run.id}")
        return results

    async def _get_dep_results_from_memory(
        self, workflow, node, node_results: Dict[str, NodeExecutionResult]
    ) -> Dict[str, Dict]:
        """Get dependency results from in-memory dict for handler context."""
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        db_nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        
        dep_ids = {e.source for e in edges if e.target == node.id}
        if not dep_ids:
            return {}

        node_type_map = {n.node_id: n.node_type for n in db_nodes}
        
        results = {}
        for dep_id in dep_ids:
            if dep_id in node_results:
                r = node_results[dep_id]
                results[dep_id] = {
                    'output': r.output,
                    'metadata': r.metadata or {},
                    'node_type': node_type_map.get(dep_id)
                }
        
        # Handle chatOutput nodes (copy from source step)
        for dep_id in dep_ids:
            if node_type_map.get(dep_id) == 'chatOutput' and dep_id not in results:
                src = next((e.source for e in edges if e.target == dep_id), None)
                if src and src in node_results:
                    r = node_results[src]
                    results[dep_id] = {
                        'output': r.output,
                        'metadata': r.metadata or {},
                        'node_type': 'chatOutput'
                    }
        
        return results

    async def _is_completed(self, workflow_run, node) -> bool:
        """Check if step already completed."""
        if node.type not in ('step', 'structuredOutput'):
            return False
        return await database_sync_to_async(
            lambda: WorkflowRunStep.objects.filter(
                workflow_run=workflow_run, step_node=node.db_node,
                status=WorkflowRunStepStatus.COMPLETED
            ).exists()
        )()

    async def _get_step(self, workflow_run, node_id) -> Optional[WorkflowRunStep]:
        """Get step by node_id."""
        return await database_sync_to_async(
            lambda: WorkflowRunStep.objects.filter(
                workflow_run=workflow_run, step_node__node_id=node_id
            ).first()
        )()

    async def _get_dep_results(self, workflow_run, workflow, node) -> Dict[str, Dict]:
        """Get results from dependency nodes."""
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        dep_ids = {e.source for e in edges if e.target == node.id}
        if not dep_ids:
            return {}

        steps = await database_sync_to_async(
            lambda: list(WorkflowRunStep.objects.filter(
                workflow_run=workflow_run, step_node__node_id__in=dep_ids,
                status__in=[WorkflowRunStepStatus.COMPLETED, WorkflowRunStepStatus.SKIPPED]
            ).select_related('step_node'))
        )()

        results = {}
        for s in steps:
            nid, ntype = s.step_node.node_id, s.step_node.node_type
            if s.status == WorkflowRunStepStatus.SKIPPED:
                results[nid] = {'output': None, 'metadata': {'skipped': True}, 'node_type': ntype}
            else:
                results[nid] = {'output': s.response, 'metadata': s.metadata or {}, 'node_type': ntype}

        # Handle chatOutput nodes
        outputs = await database_sync_to_async(
            lambda: list(workflow.nodes.filter(node_id__in=dep_ids, node_type='chatOutput'))
        )()
        for o in outputs:
            src = next((e.source for e in edges if e.target == o.node_id), None)
            if src and src in results:
                results[o.node_id] = {**results[src], 'node_type': 'chatOutput'}

        return results

    async def _get_missing_deps(self, workflow_run, node_id, workflow) -> List[str]:
        """Get unexecuted step dependencies (for single-step mode)."""
        edges = await database_sync_to_async(lambda: list(workflow.edges.all()))()
        nodes = await database_sync_to_async(lambda: list(workflow.nodes.all()))()
        
        types = {n.node_id: n.node_type for n in nodes}
        deps = [e.source for e in edges if e.target == node_id and types.get(e.source) == 'step']
        if not deps:
            return []

        done = await database_sync_to_async(
            lambda: set(WorkflowRunStep.objects.filter(
                workflow_run=workflow_run, step_node__node_id__in=deps,
                status__in=[WorkflowRunStepStatus.COMPLETED, WorkflowRunStepStatus.SKIPPED]
            ).values_list('step_node__node_id', flat=True))
        )()

        return [d for d in deps if d not in done]

    # ==================== Updates ====================

    async def _finalize_run(self, workflow_run, status, send_callback):
        """Update run status and emit completion."""
        if status in ('completed', 'failed'):
            await database_sync_to_async(
                lambda: WorkflowRun.objects.filter(id=workflow_run.id).update(ended_at=timezone.now())
            )()
        await self._emit(send_callback, WebSocketResponseService.format_workflow_execution_complete(
            workflow_run_id=workflow_run.id, status=status, ended_at=timezone.now().isoformat()
        ))

    async def _mark_skipped(self, workflow_run, node):
        """Mark step as skipped."""
        if node.type in ('step', 'structuredOutput'):
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run, step_node=node.db_node
                ).update(status=WorkflowRunStepStatus.SKIPPED, response='Skipped: routing')
            )()

    async def _fail_pending_human_step(self, workflow_run, node, send_callback):
        """Fail pending human validation steps for batch runs."""
        error_message = "Human validation is not supported in batch runs."
        if node.type in ('step', 'structuredOutput'):
            await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run,
                    step_node=node.db_node,
                    status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
                ).update(
                    status=WorkflowRunStepStatus.FAILED,
                    error=error_message
                )
            )()

        await self._emit(
            send_callback,
            WebSocketResponseService.format_workflow_error(
                node_id=node.id,
                error=error_message,
                workflow_run_id=workflow_run.id
            )
        )

    # ==================== Helpers ====================

    def _is_pending_human(self, result: NodeExecutionResult) -> bool:
        """Check if result indicates pending human validation."""
        return (not result.success and result.error == "PENDING_HUMAN_INPUT" and
                result.metadata and result.metadata.get('pending_human_validation'))

    async def _emit(self, callback, data):
        """Safe emit."""
        if callback:
            try:
                await callback(data)
            except Exception as e:
                logger.debug(f"Emit failed: {e}")


# Convenience function
async def execute_workflow_graph(workflow_run: WorkflowRun) -> Dict[str, Any]:
    return await WorkflowExecutionService().execute_workflow(workflow_run)
