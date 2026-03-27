"""
Single Step Executor

Manual mode single-step execution. Creates or reuses a partial run,
then executes one step at a time with real-time streaming.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable

from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.services.live_executor import create_send_callback
from workflows.services.workflow_run_repository import WorkflowRunRepository


logger = logging.getLogger(__name__)


class SingleStepExecutor:
    """Manual mode single-step execution."""

    def __init__(self, sio, namespace: str = '/workflow'):
        self.sio = sio
        self.namespace = namespace
        self.execution_service = WorkflowExecutionService()
        self.execution_tasks: Dict[int, asyncio.Task] = {}

    async def execute(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: int,
        step_node_id: str,
        workflow_run_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Execute a single step in manual mode with real-time streaming."""
        workflow = await WorkflowRunRepository.get_workflow(workflow_id, user)
        if not workflow:
            return {'error': 'Workflow not found or access denied'}

        # Get or create partial run
        if workflow_run_id:
            workflow_run = await WorkflowRunRepository.get_workflow_run(workflow_run_id, user)
            if not workflow_run:
                return {'error': 'Workflow run not found or access denied'}
        else:
            workflow_run = await WorkflowRunRepository.create_partial_run(workflow_id, user)
            if not workflow_run:
                return {'error': 'Failed to create workflow run'}
            workflow_run_id = workflow_run.id

        # Auto-subscribe to the run
        room_name = f'workflow_run_{workflow_run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(workflow_run_id)

        # Create send callback and start execution
        send_callback = create_send_callback(self.sio, workflow_run_id, self.namespace)
        await self._execute_step(
            workflow_run, step_node_id, workflow_run_id, send_callback
        )

        logger.info(
            f"Started single step execution: user={user.id}, "
            f"run_id={workflow_run_id}, step={step_node_id}"
        )
        return {'success': True, 'workflowRunId': workflow_run_id}

    # ==================== Internal ====================

    async def _execute_step(
        self,
        workflow_run,
        step_node_id: str,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """Start single step execution in a background task."""
        room_name = f'workflow_run_{workflow_run_id}'

        async def execute_single_step_with_streaming():
            try:
                result = await self.execution_service.execute_single_step(
                    workflow_run=workflow_run,
                    step_node_id=step_node_id,
                    send_callback=send_callback
                )

                await self.sio.emit(
                    'workflow_event',
                    WebSocketResponseService.format_workflow_execution_complete(
                        workflow_run_id=workflow_run_id,
                        status='completed' if result.get('success') else 'failed'
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            except Exception as e:
                logger.exception(f"Single step execution error: {str(e)}")
                await self.sio.emit(
                    'workflow_event',
                    WebSocketResponseService.format_workflow_error(
                        node_id=step_node_id,
                        error=str(e),
                        workflow_run_id=workflow_run_id
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        task = asyncio.create_task(execute_single_step_with_streaming())
        self.execution_tasks[workflow_run_id] = task
