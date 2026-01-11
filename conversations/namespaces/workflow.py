"""
Workflow Namespace for Socket.IO

Handles real-time workflow execution with token streaming:
- User authentication via JWT
- Workflow run subscriptions (join/leave rooms)
- Workflow execution with real-time progress updates
- Token-by-token LLM response streaming
- Human validation requests for routing nodes

This namespace enables real-time workflow monitoring, replacing
the polling-based approach with WebSocket streaming.
"""

import logging
import jwt
import asyncio
from typing import Dict, Any, Optional, Callable, Awaitable
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
import socketio

from conversations.socket_server import sio
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.namespaces.utils import detect_platform_from_socketio_environ

User = get_user_model()
logger = logging.getLogger(__name__)


class WorkflowNamespace(socketio.AsyncNamespace):
    """
    Socket.IO namespace for workflow execution streaming.

    Key features:
    - Single connection per user (not per workflow)
    - Event-based room subscriptions for workflow runs
    - Real-time token streaming during LLM execution
    - Step-by-step execution progress updates
    - Human validation request handling
    """

    def __init__(self):
        super().__init__(namespace='/workflow')

        # Session tracking: {sid: {'user': User, 'subscriptions': set(), 'platform': str}}
        self.sessions: Dict[str, Dict[str, Any]] = {}

        # Active execution tasks: {workflow_run_id: asyncio.Task}
        # Used to track and potentially cancel in-progress executions
        self.execution_tasks: Dict[int, asyncio.Task] = {}

    # ==================== Connection Lifecycle ====================

    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None):
        """
        Handle new connection with JWT authentication.

        Args:
            sid: Socket session ID
            environ: ASGI environ dict with request info
            auth: Client-provided auth data {'token': 'jwt_token'}

        Returns:
            True if auth successful, raises ConnectionRefusedError otherwise
        """
        try:
            if not auth:
                logger.warning(f"Workflow Socket.IO connect rejected: no auth provided (sid={sid})")
                raise socketio.exceptions.ConnectionRefusedError('Authentication required')

            token = auth.get('token')
            if not token:
                logger.warning(f"Workflow Socket.IO connect rejected: no token provided (sid={sid})")
                raise socketio.exceptions.ConnectionRefusedError('JWT token required')

            # Decode and validate JWT
            try:
                decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            except jwt.ExpiredSignatureError:
                raise socketio.exceptions.ConnectionRefusedError('Token expired')
            except jwt.InvalidTokenError as e:
                raise socketio.exceptions.ConnectionRefusedError(f'Invalid token: {str(e)}')

            user_id = decoded.get('user_id')
            if not user_id:
                raise socketio.exceptions.ConnectionRefusedError('Invalid token: missing user_id')

            # Fetch user from database
            user = await self._get_user(user_id)
            if not user:
                raise socketio.exceptions.ConnectionRefusedError('User not found')

            # Detect platform from Origin/Referer headers
            platform = detect_platform_from_socketio_environ(environ)

            # Store session data
            self.sessions[sid] = {
                'user': user,
                'subscriptions': set(),
                'platform': platform,
            }

            # Join user-specific room for direct notifications
            await sio.enter_room(sid, f'workflow_user_{user.id}', namespace='/workflow')

            logger.info(f"Workflow Socket.IO connected: user={user.id}, sid={sid}, platform={platform}")
            return True

        except socketio.exceptions.ConnectionRefusedError:
            raise
        except Exception as e:
            logger.exception(f"Workflow Socket.IO connect error: {str(e)}")
            raise socketio.exceptions.ConnectionRefusedError(f'Connection failed: {str(e)}')

    async def on_disconnect(self, sid: str):
        """
        Handle disconnection - cleanup session.

        Args:
            sid: Socket session ID
        """
        try:
            session = self.sessions.pop(sid, None)
            if not session:
                return

            user = session.get('user')
            subscriptions = session.get('subscriptions', set())

            logger.info(f"Workflow Socket.IO disconnected: user={user.id if user else 'None'}, sid={sid}")

            # Note: We don't cancel execution tasks on disconnect
            # The execution continues in the background and results are saved to DB
            # This allows users to reconnect and see the results

        except Exception as e:
            logger.exception(f"Workflow Socket.IO disconnect error: {str(e)}")

    # ==================== Subscription Events ====================

    async def on_subscribe_workflow_run(self, sid: str, data: dict) -> dict:
        """
        Subscribe to a workflow run room to receive execution updates.

        Args:
            sid: Socket session ID
            data: {'workflowRunId': int}

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            run_id = data.get('workflowRunId')
            if not run_id:
                return {'error': 'Missing workflowRunId'}

            user = session['user']

            # Validate user has access to this workflow run
            has_access = await self._validate_workflow_run_access(run_id, user)
            if not has_access:
                return {'error': 'Workflow run not found or access denied'}

            # Join the workflow run room
            room_name = f'workflow_run_{run_id}'
            await sio.enter_room(sid, room_name, namespace='/workflow')
            session['subscriptions'].add(run_id)

            logger.info(f"Subscribed to workflow run: user={user.id}, run_id={run_id}")

            # Send current run status
            run_status = await self._get_workflow_run_status(run_id)
            if run_status:
                await sio.emit('workflow_status', run_status, room=sid, namespace='/workflow')

            return {'success': True, 'workflowRunId': run_id}

        except Exception as e:
            logger.exception(f"Subscribe workflow run error: {str(e)}")
            return {'error': str(e)}

    async def on_unsubscribe_workflow_run(self, sid: str, data: dict) -> dict:
        """
        Unsubscribe from a workflow run room.

        Args:
            sid: Socket session ID
            data: {'workflowRunId': int}

        Returns:
            {'success': True}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            run_id = data.get('workflowRunId')
            if not run_id:
                return {'success': True}  # Nothing to unsubscribe

            # Leave the room
            room_name = f'workflow_run_{run_id}'
            await sio.leave_room(sid, room_name, namespace='/workflow')
            session['subscriptions'].discard(run_id)

            logger.info(f"Unsubscribed from workflow run: run_id={run_id}")
            return {'success': True}

        except Exception as e:
            logger.exception(f"Unsubscribe workflow run error: {str(e)}")
            return {'error': str(e)}

    async def on_subscribe_workflow(self, sid: str, data: dict) -> dict:
        """
        Subscribe to a workflow to receive execution updates.
        
        This subscribes to the workflow's latest run (if any) and returns
        current execution state. Used for socket-only execution state management.

        Args:
            sid: Socket session ID
            data: {'workflowId': int}

        Returns:
            {
                'success': True,
                'workflowId': int,
                'latestRun': {...} or None,  # Full run status with nodeStates
            }
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            workflow_id = data.get('workflowId')
            if not workflow_id:
                return {'error': 'Missing workflowId'}

            user = session['user']

            # Get the latest run for this workflow
            latest_run = await self._get_latest_workflow_run(workflow_id, user)
            
            if latest_run:
                run_id = latest_run.get('id')
                # Subscribe to this run's room
                room_name = f'workflow_run_{run_id}'
                await sio.enter_room(sid, room_name, namespace='/workflow')
                session['subscriptions'].add(run_id)
                logger.info(f"Subscribed to workflow {workflow_id} (run {run_id}): user={user.id}")
            else:
                logger.info(f"Subscribed to workflow {workflow_id} (no runs yet): user={user.id}")

            return {
                'success': True,
                'workflowId': workflow_id,
                'latestRun': latest_run,
            }

        except Exception as e:
            logger.exception(f"Subscribe workflow error: {str(e)}")
            return {'error': str(e)}

    # ==================== Execution Events ====================

    async def on_start_execution(self, sid: str, data: dict) -> dict:
        """
        Start workflow execution with real-time streaming.

        This triggers the workflow execution and streams progress updates
        to all subscribers of the workflow run room.

        Args:
            sid: Socket session ID
            data: {'workflowRunId': int} or {'workflowId': int, 'userInput': str}

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            user = session['user']
            workflow_run_id = data.get('workflowRunId')
            workflow_id = data.get('workflowId')
            user_input = data.get('userInput', '')

            # Either workflow_run_id or workflow_id must be provided
            if not workflow_run_id and not workflow_id:
                return {'error': 'Either workflowRunId or workflowId required'}

            # Import here to avoid circular imports
            from workflows.models import Workflow, WorkflowRun
            from core.services.workflow_execution_service import WorkflowExecutionService

            if workflow_id and not workflow_run_id:
                # Check for existing partial run first - continue it instead of creating new
                existing_partial = await self._get_existing_partial_run(workflow_id, user)
                if existing_partial:
                    # Convert partial run to full run and continue
                    workflow_run = await self._convert_partial_to_full_run(existing_partial, user_input)
                    workflow_run_id = workflow_run.id
                    logger.info(f"Converting partial run {workflow_run_id} to full run")
                else:
                    # Create a new workflow run
                    workflow_run = await self._create_workflow_run(workflow_id, user, user_input)
                    if not workflow_run:
                        return {'error': 'Failed to create workflow run'}
                    workflow_run_id = workflow_run.id
            else:
                # Validate access to existing run
                has_access = await self._validate_workflow_run_access(workflow_run_id, user)
                if not has_access:
                    return {'error': 'Workflow run not found or access denied'}

            # Auto-subscribe to the run
            room_name = f'workflow_run_{workflow_run_id}'
            await sio.enter_room(sid, room_name, namespace='/workflow')
            session['subscriptions'].add(workflow_run_id)

            # Create send callback for streaming
            send_callback = self._create_send_callback(workflow_run_id)

            # Start execution in background task
            execution_service = WorkflowExecutionService()

            async def execute_with_streaming():
                try:
                    await execution_service.execute_workflow(
                        workflow_run_id=workflow_run_id,
                        send_callback=send_callback
                    )
                except Exception as e:
                    logger.exception(f"Workflow execution error: {str(e)}")
                    # Send error to room
                    await sio.emit(
                        'workflow_event',
                        WebSocketResponseService.format_workflow_error(
                            node_id=None,
                            error=str(e)
                        ),
                        room=room_name,
                        namespace='/workflow'
                    )
                finally:
                    # Clean up task reference
                    self.execution_tasks.pop(workflow_run_id, None)

            # Create and track the execution task
            task = asyncio.create_task(execute_with_streaming())
            self.execution_tasks[workflow_run_id] = task

            logger.info(f"Started workflow execution: user={user.id}, run_id={workflow_run_id}")
            return {'success': True, 'workflowRunId': workflow_run_id}

        except Exception as e:
            logger.exception(f"Start execution error: {str(e)}")
            return {'error': str(e)}

    async def on_execute_single_step(self, sid: str, data: dict) -> dict:
        """
        Execute a single step in manual mode with real-time streaming.

        This triggers execution of a single workflow step and streams progress
        updates to all subscribers of the workflow run room.

        Args:
            sid: Socket session ID
            data: {
                'workflowId': int,
                'stepNodeId': str,
                'workflowRunId': int (optional - will create/reuse partial run)
            }

        Returns:
            {'success': True, 'workflowRunId': int} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            user = session['user']
            workflow_id = data.get('workflowId')
            step_node_id = data.get('stepNodeId')
            workflow_run_id = data.get('workflowRunId')

            if not workflow_id or not step_node_id:
                return {'error': 'workflowId and stepNodeId are required'}

            # Import execution service
            from core.services.workflow_execution_service import WorkflowExecutionService
            from workflows.models import Workflow, WorkflowRun
            from workflows.constants import WorkflowRunStepStatus

            # Verify workflow access
            workflow = await self._get_workflow(workflow_id, user)
            if not workflow:
                return {'error': 'Workflow not found or access denied'}

            # Get or create partial workflow run
            if workflow_run_id:
                workflow_run = await self._get_workflow_run(workflow_run_id, user)
                if not workflow_run:
                    return {'error': 'Workflow run not found or access denied'}
            else:
                # Create a new partial run
                workflow_run = await self._create_partial_workflow_run(workflow_id, user)
                if not workflow_run:
                    return {'error': 'Failed to create workflow run'}
                workflow_run_id = workflow_run.id

            # Auto-subscribe to the run
            room_name = f'workflow_run_{workflow_run_id}'
            await sio.enter_room(sid, room_name, namespace='/workflow')
            session['subscriptions'].add(workflow_run_id)

            # Create send callback for streaming
            send_callback = self._create_send_callback(workflow_run_id)

            # Execute single step in background task
            execution_service = WorkflowExecutionService()

            async def execute_single_step_with_streaming():
                try:
                    result = await execution_service.execute_single_step(
                        workflow_run=workflow_run,
                        step_node_id=step_node_id,
                        send_callback=send_callback
                    )

                    # Send execution complete event
                    await sio.emit(
                        'workflow_event',
                        WebSocketResponseService.format_workflow_execution_complete(
                            workflow_run_id=workflow_run_id,
                            status='completed' if result.get('success') else 'failed'
                        ),
                        room=room_name,
                        namespace='/workflow'
                    )
                except Exception as e:
                    logger.exception(f"Single step execution error: {str(e)}")
                    await sio.emit(
                        'workflow_event',
                        WebSocketResponseService.format_workflow_error(
                            node_id=step_node_id,
                            error=str(e)
                        ),
                        room=room_name,
                        namespace='/workflow'
                    )
                finally:
                    self.execution_tasks.pop(workflow_run_id, None)

            # Create and track the execution task
            task = asyncio.create_task(execute_single_step_with_streaming())
            self.execution_tasks[workflow_run_id] = task

            logger.info(f"Started single step execution: user={user.id}, run_id={workflow_run_id}, step={step_node_id}")
            return {'success': True, 'workflowRunId': workflow_run_id}

        except Exception as e:
            logger.exception(f"Execute single step error: {str(e)}")
            return {'error': str(e)}

    async def on_submit_validation(self, sid: str, data: dict) -> dict:
        """
        Submit human validation decision for a routing node.

        Args:
            sid: Socket session ID
            data: {
                'workflowRunId': int,
                'nodeId': str,
                'selectedRoute': str,
                'continueExecution': bool (optional, default True)
            }

        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}

            user = session['user']
            workflow_run_id = data.get('workflowRunId')
            node_id = data.get('nodeId')
            selected_route = data.get('selectedRoute')
            continue_execution = data.get('continueExecution', True)

            if not all([workflow_run_id, node_id, selected_route]):
                return {'error': 'Missing required fields: workflowRunId, nodeId, selectedRoute'}

            # Get the workflow run
            workflow_run = await self._get_workflow_run(workflow_run_id, user)
            if not workflow_run:
                return {'error': 'Workflow run not found or access denied'}

            # Import execution service
            from core.services.workflow_execution_service import WorkflowExecutionService

            # If continue_execution is True, resume execution with streaming
            if continue_execution:
                send_callback = self._create_send_callback(workflow_run_id)
                execution_service = WorkflowExecutionService()

                async def continue_with_streaming():
                    try:
                        # Use the service method to resume after human validation
                        await execution_service.resume_workflow_after_human_validation(
                            workflow_run=workflow_run,
                            node_id=node_id,
                            chosen_route=selected_route,
                            send_callback=send_callback
                        )
                    except Exception as e:
                        logger.exception(f"Continue execution error: {str(e)}")
                        room_name = f'workflow_run_{workflow_run_id}'
                        await sio.emit(
                            'workflow_event',
                            WebSocketResponseService.format_workflow_error(
                                node_id=None,
                                error=str(e)
                            ),
                            room=room_name,
                            namespace='/workflow'
                        )
                    finally:
                        self.execution_tasks.pop(workflow_run_id, None)

                task = asyncio.create_task(continue_with_streaming())
                self.execution_tasks[workflow_run_id] = task

            logger.info(f"Validation submitted: run_id={workflow_run_id}, node_id={node_id}, route={selected_route}")
            return {'success': True}

        except Exception as e:
            logger.exception(f"Submit validation error: {str(e)}")
            return {'error': str(e)}

    # ==================== Helper Methods ====================

    def _create_send_callback(self, workflow_run_id: int) -> Callable[[Dict[str, Any]], Awaitable[None]]:
        """
        Create a send callback for streaming workflow events to subscribers.

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
                await sio.emit('workflow_event', event_data, room=room_name, namespace='/workflow')
            except Exception as e:
                logger.debug(f"Send callback failed (client may have disconnected): {e}")

        return send_callback

    @sync_to_async
    def _get_user(self, user_id: int):
        """Fetch user from database."""
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @sync_to_async
    def _validate_workflow_run_access(self, run_id: int, user) -> bool:
        """
        Validate that user has access to the workflow run.

        Args:
            run_id: Workflow run ID
            user: User instance

        Returns:
            True if user has access, False otherwise
        """
        from workflows.models import WorkflowRun
        try:
            run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
            return run.workflow.user_id == user.id
        except WorkflowRun.DoesNotExist:
            return False

    @sync_to_async
    def _get_workflow_run(self, run_id: int, user):
        """
        Get workflow run instance with access validation.

        Args:
            run_id: Workflow run ID
            user: User instance

        Returns:
            WorkflowRun instance or None if not found/unauthorized
        """
        from workflows.models import WorkflowRun
        try:
            run = WorkflowRun.objects.select_related('workflow__user').get(id=run_id)
            if run.workflow.user_id == user.id:
                return run
            return None
        except WorkflowRun.DoesNotExist:
            return None

    @sync_to_async
    def _create_workflow_run(self, workflow_id: int, user, user_input: str = ''):
        """
        Create a new workflow run.

        Args:
            workflow_id: Workflow ID
            user: User instance
            user_input: Optional user input for the workflow

        Returns:
            WorkflowRun instance or None if creation failed
        """
        from workflows.models import Workflow, WorkflowRun, WorkflowRunStep, StepNodeData
        from workflows.constants import WorkflowRunStepStatus

        try:
            workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
                id=workflow_id,
                user=user
            )

            # Create the run
            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=user,
                is_partial=False
            )

            # Note: user_input is passed to execution service, not stored on WorkflowRun
            # WorkflowRun model doesn't have a metadata field

            # Get step nodes and create WorkflowRunStep for each
            step_nodes = workflow.nodes.filter(node_type='step').select_related('data_content_type')
            
            for step_node in step_nodes:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=workflow_run,
                        step_node=step_node,
                        order=step_data.step_number if step_data.step_number else 0,
                        status=WorkflowRunStepStatus.PENDING
                    )

            return workflow_run

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to create workflow run: {str(e)}")
            return None

    @sync_to_async
    def _get_workflow(self, workflow_id: int, user):
        """
        Get workflow instance with access validation.

        Args:
            workflow_id: Workflow ID
            user: User instance

        Returns:
            Workflow instance or None if not found/unauthorized
        """
        from workflows.models import Workflow
        try:
            return Workflow.objects.get(id=workflow_id, user=user)
        except Workflow.DoesNotExist:
            return None

    @sync_to_async
    def _get_existing_partial_run(self, workflow_id: int, user):
        """
        Get existing incomplete partial run for a workflow.

        Also cleans up stale partial runs that have been stuck for too long.

        Args:
            workflow_id: Workflow ID
            user: User instance

        Returns:
            WorkflowRun instance or None if no partial run exists
        """
        from datetime import timedelta
        from django.utils import timezone
        from workflows.models import WorkflowRun
        from workflows.constants import WorkflowRunStepStatus

        # Stale partial run threshold: 2 hours for partial runs
        STALE_PARTIAL_RUN_THRESHOLD_MINUTES = 120

        partial_run = WorkflowRun.active_objects.filter(
            workflow_id=workflow_id,
            user=user,
            is_partial=True,
            ended_at__isnull=True
        ).order_by('-created_at').first()

        # Clean up stale partial runs
        if partial_run and partial_run.status == WorkflowRunStepStatus.RUNNING:
            stale_threshold = timezone.now() - timedelta(minutes=STALE_PARTIAL_RUN_THRESHOLD_MINUTES)
            if partial_run.started_at and partial_run.started_at < stale_threshold:
                logger.warning(
                    f"Cleaning up stale partial run {partial_run.id} for workflow {workflow_id} "
                    f"(started at {partial_run.started_at})"
                )
                partial_run.status = WorkflowRunStepStatus.FAILED
                partial_run.ended_at = timezone.now()
                partial_run.save(update_fields=['status', 'ended_at'])
                return None

        return partial_run

    @sync_to_async
    def _convert_partial_to_full_run(self, partial_run, user_input: str = ''):
        """
        Convert a partial run to a full run and create missing WorkflowRunStep objects.

        Args:
            partial_run: The partial WorkflowRun to convert
            user_input: Optional user input for the workflow

        Returns:
            The converted WorkflowRun instance
        """
        from workflows.models import WorkflowRunStep, StepNodeData
        from workflows.constants import WorkflowRunStepStatus

        # Mark as non-partial since we're completing it in full mode
        partial_run.is_partial = False
        partial_run.save(update_fields=['is_partial'])

        # Create WorkflowRunStep objects for steps that haven't been created yet
        workflow = partial_run.workflow
        existing_step_node_ids = set(
            WorkflowRunStep.objects.filter(workflow_run=partial_run)
            .values_list('step_node__node_id', flat=True)
        )

        step_nodes = workflow.nodes.filter(node_type='step').select_related('data_content_type')
        for step_node in step_nodes:
            if step_node.node_id not in existing_step_node_ids:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=partial_run,
                        step_node=step_node,
                        order=step_data.step_number if step_data.step_number else 0,
                        status=WorkflowRunStepStatus.PENDING
                    )

        return partial_run

    @sync_to_async
    def _create_partial_workflow_run(self, workflow_id: int, user):
        """
        Create a new partial workflow run for manual mode execution.

        Args:
            workflow_id: Workflow ID
            user: User instance

        Returns:
            WorkflowRun instance or None if creation failed
        """
        from workflows.models import Workflow, WorkflowRun, WorkflowRunStep, StepNodeData
        from workflows.constants import WorkflowRunStepStatus

        try:
            workflow = Workflow.objects.prefetch_related('nodes', 'edges').get(
                id=workflow_id,
                user=user
            )

            # Create a partial run
            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=user,
                is_partial=True
            )

            # Get step nodes and create WorkflowRunStep for each
            step_nodes = workflow.nodes.filter(node_type='step').select_related('data_content_type')

            for step_node in step_nodes:
                step_data = step_node.data_object
                if step_data and isinstance(step_data, StepNodeData):
                    WorkflowRunStep.objects.create(
                        workflow_run=workflow_run,
                        step_node=step_node,
                        order=step_data.step_number if step_data.step_number else 0,
                        status=WorkflowRunStepStatus.PENDING
                    )

            return workflow_run

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to create partial workflow run: {str(e)}")
            return None

    @sync_to_async
    def _get_workflow_run_status(self, run_id: int) -> Optional[Dict[str, Any]]:
        """
        Get the current status of a workflow run.

        Args:
            run_id: Workflow run ID

        Returns:
            Status dictionary or None if not found
        """
        from workflows.models import WorkflowRun
        from workflows.api.serializers import WorkflowRunSerializer

        try:
            run = WorkflowRun.objects.prefetch_related(
                'steps__step_node'
            ).get(id=run_id)

            # Use serializer for consistent formatting
            serializer = WorkflowRunSerializer(run)
            return {
                'type': 'workflow_status',
                **serializer.data
            }
        except WorkflowRun.DoesNotExist:
            return None

    @sync_to_async
    def _get_latest_workflow_run(self, workflow_id: int, user) -> Optional[Dict[str, Any]]:
        """
        Get the latest workflow run for a workflow with full execution state.

        Also cleans up stale runs that have been stuck in "running" status.

        Args:
            workflow_id: Workflow ID
            user: User instance

        Returns:
            Full run data with nodeStates or None if no runs exist
        """
        from datetime import timedelta
        from django.utils import timezone
        from workflows.models import Workflow, WorkflowRun
        from workflows.api.serializers import WorkflowRunV2Serializer
        from workflows.constants import WorkflowRunStepStatus

        # Stale run threshold: runs stuck in "running" for more than 30 minutes
        STALE_RUN_THRESHOLD_MINUTES = 30

        try:
            # Verify workflow access
            workflow = Workflow.objects.get(id=workflow_id, user=user)

            # Get the latest run
            latest_run = WorkflowRun.objects.filter(
                workflow=workflow
            ).prefetch_related(
                'steps__step_node'
            ).order_by('-created_at').first()

            if not latest_run:
                return None

            # Check for stale run and clean up
            if latest_run.status == WorkflowRunStepStatus.RUNNING:
                stale_threshold = timezone.now() - timedelta(minutes=STALE_RUN_THRESHOLD_MINUTES)
                # Check if the run started more than threshold ago
                if latest_run.started_at and latest_run.started_at < stale_threshold:
                    logger.warning(
                        f"Cleaning up stale run {latest_run.id} for workflow {workflow_id} "
                        f"(started at {latest_run.started_at})"
                    )
                    latest_run.status = WorkflowRunStepStatus.FAILED
                    latest_run.ended_at = timezone.now()
                    latest_run.save(update_fields=['status', 'ended_at'])

            # Use V2 serializer for full nodeStates
            serializer = WorkflowRunV2Serializer(latest_run)
            return serializer.data

        except Workflow.DoesNotExist:
            logger.warning(f"Workflow not found: id={workflow_id}, user={user.id}")
            return None
        except Exception as e:
            logger.exception(f"Failed to get latest workflow run: {str(e)}")
            return None


# Create the namespace instance
workflow_namespace = WorkflowNamespace()
