"""
Workflow Coordinator Service

Orchestrates workflow execution lifecycle for WebSocket connections:
- Workflow run creation and subscription
- Execution initiation (full and single-step)
- Human validation submission and workflow resumption
- Real-time event streaming to subscribers

This coordinator encapsulates business logic that was previously embedded
in WorkflowNamespace event handlers. It follows the same pattern as
conversations/services/message_coordinator.py.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable

from asgiref.sync import sync_to_async
from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.api.serializers import WorkflowRunV2Serializer
from workflows.services.workflow_run_service import (
    validate_workflow_run_access,
    get_workflow_run,
    get_workflow,
    create_workflow_run,
    create_partial_workflow_run,
    get_existing_partial_run,
    convert_partial_to_full_run,
    get_workflow_run_for_status,
    get_latest_workflow_run_obj,
)


logger = logging.getLogger(__name__)


class WorkflowCoordinator:
    """
    Coordinates workflow execution operations for WebSocket consumers.

    This class manages:
    - Workflow run lifecycle (creation, subscription, status)
    - Execution orchestration (full workflow, single step, validation)
    - Background task tracking for in-progress executions
    - Event streaming callbacks for real-time updates

    Similar to MessageCoordinator for conversations, this provides a clean
    separation between WebSocket handling (WorkflowNamespace) and business
    logic (this coordinator).
    """

    def __init__(self, sio, namespace: str = '/workflow'):
        """
        Initialize the workflow coordinator.

        Args:
            sio: Socket.IO server instance for emitting events
            namespace: Socket.IO namespace for workflow events
        """
        self.sio = sio
        self.namespace = namespace
        self.execution_service = WorkflowExecutionService()

        # Active execution tasks: {workflow_run_id: asyncio.Task}
        # Used to track and potentially cancel in-progress executions
        self.execution_tasks: Dict[int, asyncio.Task] = {}

    # ==================== Subscription Operations ====================

    async def subscribe_workflow_run(
        self,
        sid: str,
        run_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Subscribe to a workflow run room and return current status.

        Args:
            sid: Socket session ID
            run_id: Workflow run ID to subscribe to
            user: User instance
            session: Session dict to update subscriptions

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        # Validate access
        has_access = await validate_workflow_run_access(run_id, user)
        if not has_access:
            return {'error': 'Workflow run not found or access denied'}

        # Join room
        room_name = f'workflow_run_{run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(run_id)

        logger.info(f"Subscribed to workflow run: user={user.id}, run_id={run_id}")

        # Send current status
        workflow_run = await get_workflow_run_for_status(run_id)
        if workflow_run:
            # Serializer accesses related models, must run in sync context
            run_status = await sync_to_async(
                lambda: {
                    'type': 'workflow_status',
                    **WorkflowRunV2Serializer(workflow_run).data
                }
            )()
            await self.sio.emit(
                'workflow_status',
                run_status,
                room=sid,
                namespace=self.namespace
            )

        return {'success': True, 'workflowRunId': run_id}

    async def subscribe_workflow(
        self,
        sid: str,
        workflow_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Subscribe to a workflow's latest run.

        Returns current execution state for socket-only state management.

        Args:
            sid: Socket session ID
            workflow_id: Workflow ID to subscribe to
            user: User instance
            session: Session dict to update subscriptions

        Returns:
            {
                'success': True,
                'workflowId': int,
                'latestRun': {...} or None,
            }
        """
        workflow_run = await get_latest_workflow_run_obj(workflow_id, user)

        # Serialize if we have a run (must run in sync context due to ORM access)
        latest_run_data = None
        if workflow_run:
            latest_run_data = await sync_to_async(
                lambda: WorkflowRunV2Serializer(workflow_run).data
            )()
            run_id = latest_run_data.get('id')
            room_name = f'workflow_run_{run_id}'
            await self.sio.enter_room(sid, room_name, namespace=self.namespace)
            session['subscriptions'].add(run_id)
            logger.info(
                f"Subscribed to workflow {workflow_id} (run {run_id}): user={user.id}"
            )
        else:
            logger.info(
                f"Subscribed to workflow {workflow_id} (no runs yet): user={user.id}"
            )

        return {
            'success': True,
            'workflowId': workflow_id,
            'latestRun': latest_run_data,
        }

    # ==================== Execution Operations ====================

    async def start_execution(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_run_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        user_input: str = ''
    ) -> Dict[str, Any]:
        """
        Start workflow execution with real-time streaming.

        Handles both new workflow execution and resuming partial runs.

        Args:
            sid: Socket session ID
            user: User instance
            session: Session dict to update subscriptions
            workflow_run_id: Existing workflow run ID (optional)
            workflow_id: Workflow ID to create run for (optional)
            user_input: Optional user input for the workflow

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        if not workflow_run_id and not workflow_id:
            return {'error': 'Either workflowRunId or workflowId required'}

        # Get or create workflow run
        if workflow_id and not workflow_run_id:
            # Check for existing partial run first - continue it instead of creating new
            existing_partial = await get_existing_partial_run(workflow_id, user)
            if existing_partial:
                # Convert partial run to full run and continue
                workflow_run = await convert_partial_to_full_run(
                    existing_partial, user_input
                )
                workflow_run_id = workflow_run.id
                logger.info(f"Converting partial run {workflow_run_id} to full run")
            else:
                # Create a new workflow run
                workflow_run = await create_workflow_run(workflow_id, user, user_input)
                if not workflow_run:
                    return {'error': 'Failed to create workflow run'}
                workflow_run_id = workflow_run.id
        else:
            # Validate access to existing run
            has_access = await validate_workflow_run_access(workflow_run_id, user)
            if not has_access:
                return {'error': 'Workflow run not found or access denied'}

        # Auto-subscribe to the run
        room_name = f'workflow_run_{workflow_run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(workflow_run_id)

        # Create send callback and start execution
        send_callback = self._create_send_callback(workflow_run_id)
        await self._start_execution_task(workflow_run_id, send_callback)

        logger.info(
            f"Started workflow execution: user={user.id}, run_id={workflow_run_id}"
        )
        return {'success': True, 'workflowRunId': workflow_run_id}

    async def execute_single_step(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: int,
        step_node_id: str,
        workflow_run_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Execute a single step in manual mode with real-time streaming.

        Args:
            sid: Socket session ID
            user: User instance
            session: Session dict to update subscriptions
            workflow_id: Workflow ID
            step_node_id: Node ID of the step to execute
            workflow_run_id: Existing workflow run ID (optional)

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        # Verify workflow access
        workflow = await get_workflow(workflow_id, user)
        if not workflow:
            return {'error': 'Workflow not found or access denied'}

        # Get or create partial run
        if workflow_run_id:
            workflow_run = await get_workflow_run(workflow_run_id, user)
            if not workflow_run:
                return {'error': 'Workflow run not found or access denied'}
        else:
            # Create a new partial run
            workflow_run = await create_partial_workflow_run(workflow_id, user)
            if not workflow_run:
                return {'error': 'Failed to create workflow run'}
            workflow_run_id = workflow_run.id

        # Auto-subscribe to the run
        room_name = f'workflow_run_{workflow_run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(workflow_run_id)

        # Create send callback and start execution
        send_callback = self._create_send_callback(workflow_run_id)
        await self._start_single_step_task(
            workflow_run, step_node_id, workflow_run_id, send_callback
        )

        logger.info(
            f"Started single step execution: user={user.id}, "
            f"run_id={workflow_run_id}, step={step_node_id}"
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
        """
        Submit human validation decision for a routing node.

        Args:
            user: User instance
            workflow_run_id: Workflow run ID
            node_id: Node ID of the routing node
            selected_route: Route name chosen by the user
            continue_execution: Whether to continue execution after validation

        Returns:
            {'success': True} or {'error': 'message'}
        """
        workflow_run = await get_workflow_run(workflow_run_id, user)
        if not workflow_run:
            return {'error': 'Workflow run not found or access denied'}

        if continue_execution:
            send_callback = self._create_send_callback(workflow_run_id)
            await self._start_resume_task(
                workflow_run, node_id, selected_route, workflow_run_id, send_callback
            )

        logger.info(
            f"Validation submitted: run_id={workflow_run_id}, "
            f"node_id={node_id}, route={selected_route}"
        )
        return {'success': True}

    # ==================== Internal Helper Methods ====================

    def _create_send_callback(
        self,
        workflow_run_id: int
    ) -> Callable[[Dict[str, Any]], Awaitable[None]]:
        """
        Create send callback for streaming workflow events to subscribers.

        The callback emits events to the workflow run room so all subscribers
        receive real-time updates.

        Args:
            workflow_run_id: The workflow run ID to broadcast to

        Returns:
            Async callback function that emits events to the room
        """
        room_name = f'workflow_run_{workflow_run_id}'

        async def send_callback(event_data: Dict[str, Any]):
            """Send workflow event to all room subscribers."""
            try:
                await self.sio.emit(
                    'workflow_event',
                    event_data,
                    room=room_name,
                    namespace=self.namespace
                )
            except Exception as e:
                logger.debug(
                    f"Send callback failed (client may have disconnected): {e}"
                )

        return send_callback

    async def _start_execution_task(
        self,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """
        Start full workflow execution in a background task.

        Args:
            workflow_run_id: Workflow run ID to execute
            send_callback: Callback for streaming events
        """
        room_name = f'workflow_run_{workflow_run_id}'

        async def execute_with_streaming():
            try:
                await self.execution_service.execute_workflow(
                    workflow_run_id=workflow_run_id,
                    send_callback=send_callback
                )
            except Exception as e:
                logger.exception(f"Workflow execution error: {str(e)}")
                # Send error to room
                await self.sio.emit(
                    'workflow_event',
                    WebSocketResponseService.format_workflow_error(
                        node_id=None,
                        error=str(e)
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                # Clean up task reference
                self.execution_tasks.pop(workflow_run_id, None)

        # Create and track the execution task
        task = asyncio.create_task(execute_with_streaming())
        self.execution_tasks[workflow_run_id] = task

    async def _start_single_step_task(
        self,
        workflow_run,
        step_node_id: str,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """
        Start single step execution in a background task.

        Args:
            workflow_run: WorkflowRun instance
            step_node_id: Node ID of the step to execute
            workflow_run_id: Workflow run ID
            send_callback: Callback for streaming events
        """
        room_name = f'workflow_run_{workflow_run_id}'

        async def execute_single_step_with_streaming():
            try:
                result = await self.execution_service.execute_single_step(
                    workflow_run=workflow_run,
                    step_node_id=step_node_id,
                    send_callback=send_callback
                )

                # Send execution complete event
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
                        error=str(e)
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        # Create and track the execution task
        task = asyncio.create_task(execute_single_step_with_streaming())
        self.execution_tasks[workflow_run_id] = task

    async def _start_resume_task(
        self,
        workflow_run,
        node_id: str,
        selected_route: str,
        workflow_run_id: int,
        send_callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """
        Start workflow resumption after validation in a background task.

        Args:
            workflow_run: WorkflowRun instance
            node_id: Node ID of the routing node
            selected_route: Route name chosen by the user
            workflow_run_id: Workflow run ID
            send_callback: Callback for streaming events
        """
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
                        error=str(e)
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        # Create and track the execution task
        task = asyncio.create_task(continue_with_streaming())
        self.execution_tasks[workflow_run_id] = task
