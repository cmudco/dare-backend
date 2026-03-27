"""
Live Executor

Full workflow execution lifecycle: start, resume after validation.
Handles run creation/lookup, room subscription, and background task management.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable

from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.services.workflow_run_repository import WorkflowRunRepository


logger = logging.getLogger(__name__)


def create_send_callback(sio, workflow_run_id: int, namespace: str) -> Callable[[Dict[str, Any]], Awaitable[None]]:
    """Create send callback for streaming workflow events to a run room."""
    room_name = f'workflow_run_{workflow_run_id}'

    async def send_callback(event_data: Dict[str, Any]):
        try:
            await sio.emit(
                'workflow_event',
                event_data,
                room=room_name,
                namespace=namespace
            )
        except Exception as e:
            logger.debug(
                f"Send callback failed (client may have disconnected): {e}"
            )

    return send_callback


class LiveExecutor:
    """Full workflow execution lifecycle."""

    def __init__(self, sio, namespace: str = '/workflow'):
        self.sio = sio
        self.namespace = namespace
        self.execution_service = WorkflowExecutionService()
        self.execution_tasks: Dict[int, asyncio.Task] = {}

    async def start(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_run_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        user_input: str = ''
    ) -> Dict[str, Any]:
        """Start workflow execution with real-time streaming."""
        if not workflow_run_id and not workflow_id:
            return {'error': 'Either workflowRunId or workflowId required'}

        # Get or create workflow run
        if workflow_id and not workflow_run_id:
            existing_partial = await WorkflowRunRepository.get_existing_partial_run(workflow_id, user)
            if existing_partial:
                workflow_run = await WorkflowRunRepository.convert_partial_to_full(
                    existing_partial, user_input
                )
                workflow_run_id = workflow_run.id
                logger.info(f"Converting partial run {workflow_run_id} to full run")
            else:
                workflow_run = await WorkflowRunRepository.create_full_run(workflow_id, user, user_input)
                if not workflow_run:
                    return {'error': 'Failed to create workflow run'}
                workflow_run_id = workflow_run.id
        else:
            has_access = await WorkflowRunRepository.validate_access(workflow_run_id, user)
            if not has_access:
                return {'error': 'Workflow run not found or access denied'}

        # Auto-subscribe to the run
        room_name = f'workflow_run_{workflow_run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(workflow_run_id)

        # Create send callback and start execution
        send_callback = create_send_callback(self.sio, workflow_run_id, self.namespace)
        await self._execute(workflow_run_id, send_callback)

        logger.info(
            f"Started workflow execution: user={user.id}, run_id={workflow_run_id}"
        )
        return {'success': True, 'workflowRunId': workflow_run_id}

    async def submit_validation(
        self,
        user,
        workflow_run_id: int,
        node_id: str,
        selected_route: str,
        continue_execution: bool = True
    ) -> Dict[str, Any]:
        """Submit human validation decision for a routing node."""
        workflow_run = await WorkflowRunRepository.get_workflow_run(workflow_run_id, user)
        if not workflow_run:
            return {'error': 'Workflow run not found or access denied'}

        if continue_execution:
            send_callback = create_send_callback(self.sio, workflow_run_id, self.namespace)
            await self._resume(
                workflow_run, node_id, selected_route, workflow_run_id, send_callback
            )

        logger.info(
            f"Validation submitted: run_id={workflow_run_id}, "
            f"node_id={node_id}, route={selected_route}"
        )
        return {'success': True}

    # ==================== Internal ====================

    async def _execute(
        self,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """Start full workflow execution in a background task."""
        room_name = f'workflow_run_{workflow_run_id}'

        async def execute_with_streaming():
            try:
                await self.execution_service.execute_workflow(
                    workflow_run_id=workflow_run_id,
                    send_callback=send_callback
                )
            except Exception as e:
                logger.exception(f"Workflow execution error: {str(e)}")
                await self.sio.emit(
                    'workflow_event',
                    WebSocketResponseService.format_workflow_error(
                        node_id=None,
                        error=str(e),
                        workflow_run_id=workflow_run_id
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        task = asyncio.create_task(execute_with_streaming())
        self.execution_tasks[workflow_run_id] = task

    async def _resume(
        self,
        workflow_run,
        node_id: str,
        selected_route: str,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """Start workflow resumption after validation in a background task."""
        room_name = f'workflow_run_{workflow_run_id}'

        async def continue_with_streaming():
            try:
                await self.execution_service.resume_workflow_after_human_validation(
                    workflow_run=workflow_run,
                    node_id=node_id,
                    chosen_route=selected_route,
                    send_callback=send_callback
                )
            except Exception as e:
                logger.exception(f"Continue execution error: {str(e)}")
                await self.sio.emit(
                    'workflow_event',
                    WebSocketResponseService.format_workflow_error(
                        node_id=None,
                        error=str(e),
                        workflow_run_id=workflow_run_id
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        task = asyncio.create_task(continue_with_streaming())
        self.execution_tasks[workflow_run_id] = task
