"""
Workflow Execution Service

Thin orchestrator: load graph → order nodes → run loop → finalize.
Graph loading, routing, and DB ops are delegated to dedicated modules.
"""
import logging
from typing import Dict, List, Optional

from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.services.websocket_response_service import WebSocketResponseService
from workflows.handlers import (
    node_handler_registry, NodeExecutionContext, NodeExecutionResult,
    ExecutionNode, ExecutionResult,
)
from workflows.models import WorkflowRun
from workflows.services.execution_routing import should_execute, get_dep_results
from workflows.services.workflow_graph import WorkflowGraph, load_graph, get_ordered_exec_nodes
from workflows.services.workflow_run_repository import WorkflowRunRepository

logger = logging.getLogger(__name__)


class WorkflowExecutionService:
    """Workflow executor — thin orchestrator over graph, routing, and repository."""

    # ==================== Public API ====================

    async def execute_workflow(
        self,
        workflow_run: WorkflowRun = None,
        workflow_run_id: int = None,
        send_callback=None,
        batch_file_id: Optional[int] = None
    ) -> ExecutionResult:
        """Execute workflow from start or resume from where it left off."""
        try:
            if workflow_run is None:
                workflow_run = await database_sync_to_async(
                    lambda: WorkflowRun.objects.select_related('workflow').get(id=workflow_run_id)
                )()

            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            graph = await load_graph(workflow)

            effective_batch_file_id = batch_file_id or workflow_run.batch_file_id
            start_connected_step_node_ids = []
            if effective_batch_file_id:
                start_connected_step_node_ids = await self._get_start_connected_step_node_ids(graph, workflow)

            exec_nodes = get_ordered_exec_nodes(graph)
            if not exec_nodes:
                return ExecutionResult(success=False, error='No nodes found')

            result = await self._run_nodes(
                workflow_run, graph, exec_nodes, send_callback,
                batch_file_id=effective_batch_file_id,
                start_connected_step_node_ids=start_connected_step_node_ids,
            )

            if not result.pending_human_input:
                status = 'completed' if result.success else 'failed'
                await WorkflowRunRepository.finalize_run(workflow_run, status)
                await self._emit(send_callback, WebSocketResponseService.format_workflow_execution_complete(
                    workflow_run_id=workflow_run.id, status=status, ended_at=timezone.now().isoformat()
                ))

            return result

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            if workflow_run is not None:
                await WorkflowRunRepository.mark_run_failed(workflow_run, error_message=str(e))
            await self._emit(send_callback, WebSocketResponseService.format_workflow_error(
                node_id=None,
                error=str(e),
                workflow_run_id=workflow_run.id if workflow_run else None
            ))
            return ExecutionResult(success=False, error=str(e))

    async def resume_workflow_after_human_validation(
        self,
        workflow_run: WorkflowRun,
        node_id: str,
        chosen_route: str,
        send_callback=None
    ) -> ExecutionResult:
        """Resume after human routing decision — update step and re-run."""
        try:
            updated = await WorkflowRunRepository.complete_human_validation(
                workflow_run, node_id, chosen_route
            )
            if not updated:
                return ExecutionResult(success=False, error='No pending validation found')

            logger.info(f"Human validation: run={workflow_run.id}, node={node_id}, route={chosen_route}")
            return await self.execute_workflow(workflow_run=workflow_run, send_callback=send_callback)

        except Exception as e:
            logger.error(f"Resume failed: {e}", exc_info=True)
            return ExecutionResult(success=False, error=str(e))

    async def execute_single_step(
        self,
        workflow_run: WorkflowRun,
        step_node_id: str,
        send_callback=None
    ) -> ExecutionResult:
        """Execute single step (manual mode)."""
        try:
            workflow = await database_sync_to_async(lambda: workflow_run.workflow)()
            graph = await load_graph(workflow)

            step_node = graph.node_map.get(step_node_id)
            if not step_node:
                return ExecutionResult(success=False, error=f'Step {step_node_id} not found')

            # Check dependencies
            dep_node_ids = [
                e.source for e in graph.edge_map_by_target.get(step_node_id, [])
                if graph.type_map.get(e.source) == 'step'
            ]
            missing = await WorkflowRunRepository.get_missing_deps(
                workflow_run, step_node_id, dep_node_ids
            )
            if missing:
                return ExecutionResult(success=False, error=f'Missing deps: {", ".join(missing)}')

            exec_node = ExecutionNode(
                id=step_node.node_id,
                type=step_node.node_type,
                label=getattr(step_node._prefetched_data_object, 'label', '') or '',
                db_node=step_node,
            )

            node_results = await WorkflowRunRepository.load_existing_results(workflow_run)

            result = await self._execute_node(
                workflow_run, graph, exec_node, node_results, send_callback,
                is_single_step_execution=True
            )

            await self._emit(send_callback, WebSocketResponseService.format_workflow_execution_complete(
                workflow_run_id=workflow_run.id, status='completed' if result.success else 'failed'
            ))

            return ExecutionResult(success=result.success, error=result.error, executed_nodes=1)

        except Exception as e:
            logger.error(f"Single step failed: {e}", exc_info=True)
            return ExecutionResult(success=False, error=str(e))

    # ==================== Core Loop ====================

    async def _run_nodes(
        self,
        workflow_run,
        graph: WorkflowGraph,
        nodes: List[ExecutionNode],
        send_callback,
        batch_file_id: Optional[int] = None,
        start_connected_step_node_ids: Optional[List[str]] = None
    ) -> ExecutionResult:
        """Main loop: iterate nodes, skip completed, check routing, execute."""
        executed, skipped, failed = 0, 0, 0
        start_connected_step_node_ids = start_connected_step_node_ids or []
        is_batch = bool(batch_file_id or getattr(workflow_run, 'batch_run_id', None))

        node_results = await WorkflowRunRepository.load_existing_results(workflow_run)

        for node in nodes:
            if node.id in node_results and not node_results[node.id].metadata.get('skipped'):
                executed += 1
                continue

            if not should_execute(graph, node, node_results):
                skipped += 1
                node_results[node.id] = NodeExecutionResult(
                    success=True, output=None,
                    metadata={'skipped': True, 'reason': 'routing_decision'}
                )
                if node.type in ('step', 'structuredOutput'):
                    await WorkflowRunRepository.mark_node_skipped(workflow_run, node.db_node)
                continue

            result = await self._execute_node(
                workflow_run, graph, node, node_results, send_callback,
                batch_file_id=batch_file_id,
                is_start_connected=node.id in start_connected_step_node_ids,
            )
            node_results[node.id] = result
            executed += 1

            if self._is_pending_human(result):
                if is_batch:
                    await WorkflowRunRepository.fail_pending_human_step(workflow_run, node.db_node)
                    await self._emit(send_callback, WebSocketResponseService.format_workflow_error(
                        node_id=node.id,
                        error="Human validation is not supported in batch runs.",
                        workflow_run_id=workflow_run.id,
                    ))
                    failed += 1
                    return ExecutionResult(
                        success=False, executed_nodes=executed,
                        skipped_nodes=skipped, failed_nodes=failed,
                    )
                await self._emit(send_callback, WebSocketResponseService.format_workflow_validation_required(
                    node_id=node.id,
                    routes=result.metadata.get('available_routes', []),
                    context={'label': result.metadata.get('label'),
                             'customPrompt': result.metadata.get('custom_prompt'),
                             'aiAnalysis': result.metadata.get('ai_analysis')},
                    ai_recommendation=result.metadata.get('ai_recommendation'),
                    workflow_run_id=workflow_run.id
                ))
                logger.info(f"Sent validation_required for node {node.id}")
                return ExecutionResult(
                    success=False, pending_human_input=True,
                    executed_nodes=executed, skipped_nodes=skipped,
                )

            if not result.success:
                failed += 1
                if result.error and node.type not in ('step', 'structuredOutput'):
                    await WorkflowRunRepository.mark_node_failed(
                        workflow_run, node.db_node, result.error
                    )

        return ExecutionResult(
            success=failed == 0, executed_nodes=executed,
            skipped_nodes=skipped, failed_nodes=failed,
        )

    async def _execute_node(
        self, workflow_run, graph: WorkflowGraph, node, node_results, send_callback,
        is_single_step_execution: bool = False,
        batch_file_id: Optional[int] = None,
        is_start_connected: bool = False,
    ) -> NodeExecutionResult:
        """Execute single node via handler registry."""
        previous = get_dep_results(graph, node, node_results)
        context = NodeExecutionContext(
            workflow_run=workflow_run,
            previous_results=previous,
            send_callback=send_callback,
            is_single_step_execution=is_single_step_execution,
            batch_file_id=batch_file_id,
            is_start_connected=is_start_connected,
        )
        return await node_handler_registry.execute_node(node, context)

    # ==================== Helpers ====================

    async def _get_start_connected_step_node_ids(
        self, graph: WorkflowGraph, workflow
    ) -> List[str]:
        """Get node_ids of step nodes directly connected to root start node."""
        start_node = await database_sync_to_async(lambda: workflow.root_start_node)()
        if not start_node:
            return []

        step_node_ids = {nid for nid, ntype in graph.type_map.items() if ntype == 'step'}
        return [
            e.target for e in graph.edges
            if e.source == start_node.node_id and e.target in step_node_ids
        ]

    @staticmethod
    def _is_pending_human(result: NodeExecutionResult) -> bool:
        """Check if result indicates pending human validation."""
        return (not result.success and result.error == "PENDING_HUMAN_INPUT" and
                result.metadata and result.metadata.get('pending_human_validation'))

    @staticmethod
    async def _emit(callback, data):
        """Safe emit."""
        if callback:
            try:
                await callback(data)
            except Exception as e:
                logger.debug(f"Emit failed: {e}")


# Convenience function
async def execute_workflow_graph(workflow_run: WorkflowRun) -> ExecutionResult:
    return await WorkflowExecutionService().execute_workflow(workflow_run=workflow_run)
