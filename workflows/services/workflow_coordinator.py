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
from typing import Dict, Any, Optional, Callable, Awaitable, List

from asgiref.sync import sync_to_async
from djangorestframework_camel_case.util import camelize
from django_rq import get_queue
from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.workflow_execution_service import WorkflowExecutionService
from files.constants import FileStatus
from files.models import File
from workflows.api.serializers import WorkflowRunV2Serializer
from workflows.models import BatchRun, WorkflowRun
from workflows.constants import WorkflowRunStepStatus
from workflows.tasks import run_batch_workflow
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

# Fields to preserve from camelize() key mangling — these contain
# user-generated dict keys (node IDs) that must not be camelCase-converted.
# Used for socket emissions. REST uses the same setting via JSON_UNDERSCOREIZE.
_CAMELIZE_IGNORE_FIELDS = ('nodeStates',)


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

    # ==================== Batch Operations ====================

    async def start_batch_execution(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: Optional[int],
        file_ids: List[int]
    ) -> Dict[str, Any]:
        """
        Start batch execution by enqueuing a workflow run for each file.

        Args:
            sid: Socket session ID
            user: User instance
            session: Session dict
            workflow_id: Workflow ID
            file_ids: List of file IDs

        Returns:
            {'success': True, 'batchId': int} or {'error': 'message'}
        """
        if not workflow_id:
            return {'error': 'Missing workflowId'}
        if not file_ids:
            return {'error': 'No files selected for batch execution'}

        workflow = await get_workflow(workflow_id, user)
        if not workflow:
            return {'error': 'Workflow not found or access denied'}

        valid_files, invalid_ids = await self._get_valid_batch_files(user, file_ids)
        if invalid_ids:
            return {
                'error': 'Some files are not eligible for batch execution',
                'invalidFileIds': invalid_ids
            }

        batch_run = await sync_to_async(
            lambda: BatchRun.objects.create(
                workflow=workflow,
                user=user,
                total_files=len(valid_files)
            )
        )()

        room_name = f'workflow_user_{user.id}'
        await self.sio.emit(
            'workflow_event',
            WebSocketResponseService.format_batch_started(
                batch_id=batch_run.id,
                total_files=len(valid_files),
                workflow_id=workflow_id
            ),
            room=room_name,
            namespace=self.namespace
        )

        queue = get_queue()
        for index, file_obj in enumerate(valid_files, start=1):
            queue.enqueue(
                run_batch_workflow,
                batch_run.id,
                workflow_id,
                user.id,
                file_obj.id,
                index,
                len(valid_files)
            )

        logger.info(
            f"Started batch execution: user={user.id}, workflow_id={workflow_id}, "
            f"batch_id={batch_run.id}, total_files={len(valid_files)}"
        )
        return {'success': True, 'batchId': batch_run.id}

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
                    **camelize(WorkflowRunV2Serializer(workflow_run).data,
                               ignore_fields=_CAMELIZE_IGNORE_FIELDS)
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
                lambda: camelize(WorkflowRunV2Serializer(workflow_run).data,
                                 ignore_fields=_CAMELIZE_IGNORE_FIELDS)
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

        latest_batch_run = await self._get_latest_batch_run_summary(
            workflow_id=workflow_id,
            user=user
        )

        return {
            'success': True,
            'workflowId': workflow_id,
            'latestRun': latest_run_data,
            'latestBatchRun': latest_batch_run,
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
                        error=str(e),
                        workflow_run_id=workflow_run_id
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
                        error=str(e),
                        workflow_run_id=workflow_run_id
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
                        error=str(e),
                        workflow_run_id=workflow_run_id
                    ),
                    room=room_name,
                    namespace=self.namespace
                )
            finally:
                self.execution_tasks.pop(workflow_run_id, None)

        # Create and track the execution task
        task = asyncio.create_task(continue_with_streaming())
        self.execution_tasks[workflow_run_id] = task

    # ==================== Batch Helpers ====================

    async def _get_valid_batch_files(
        self,
        user,
        file_ids: List[int]
    ) -> tuple[List[File], List[int]]:
        """Validate batch files and return ordered list with invalid IDs."""
        if not file_ids:
            return [], []

        def _fetch_files():
            files = list(
                File.active_objects.filter(
                    id__in=file_ids,
                    user=user,
                    status=FileStatus.PROCESSED,
                    is_media=False
                )
            )
            file_map = {file_obj.id: file_obj for file_obj in files if (
                file_obj.vector_db_source is None or file_obj.vector_db_source == user.vector_db
            )}
            ordered_files: List[File] = []
            invalid_ids: List[int] = []
            seen_ids = set()
            for file_id in file_ids:
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)
                if file_id in file_map:
                    ordered_files.append(file_map[file_id])
                else:
                    invalid_ids.append(file_id)
            return ordered_files, invalid_ids

        return await sync_to_async(_fetch_files)()

    async def _get_latest_batch_run_summary(
        self,
        workflow_id: int,
        user
    ) -> Optional[Dict[str, Any]]:
        """Return summary of latest batch run for a workflow (if any)."""
        def _fetch_summary():
            batch_run = (
                BatchRun.objects.filter(workflow_id=workflow_id, user=user)
                .order_by('-created_at')
                .first()
            )
            if not batch_run:
                return None

            runs = (
                WorkflowRun.objects.filter(batch_run=batch_run)
                .select_related('batch_file')
                .prefetch_related('steps')
                .order_by('created_at')
            )

            file_statuses = []
            for index, run in enumerate(runs, start=1):
                file_obj = run.batch_file
                file_name = "Unknown file"
                if file_obj:
                    file_name = file_obj.name or file_obj.file.name

                if run.status in (
                    WorkflowRunStepStatus.RUNNING,
                    WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
                ):
                    status = 'running'
                elif run.status == WorkflowRunStepStatus.FAILED:
                    status = 'failed'
                else:
                    status = 'completed'

                file_statuses.append({
                    'fileId': run.batch_file_id or 0,
                    'fileName': file_name,
                    'status': status,
                    'workflowRunId': run.id,
                    'index': index,
                })

            return {
                'batchId': batch_run.id,
                'workflowId': workflow_id,
                'status': batch_run.status,
                'totalFiles': batch_run.total_files,
                'completedCount': batch_run.completed_count,
                'failedCount': batch_run.failed_count,
                'fileStatuses': file_statuses,
            }

        return await sync_to_async(_fetch_summary)()
