"""
Workflow Namespace for Socket.IO

Thin WebSocket handler for workflow execution events.
Delegates all business logic to WorkflowCoordinator.

Responsibilities:
- Connection lifecycle (authentication, session management)
- Event routing to coordinator
- Unsubscription handling

This architecture follows the same pattern as ChatConsumer -> MessageCoordinator,
providing clean separation between WebSocket handling and business logic.
"""

import logging
import jwt
from typing import Dict, Any, Optional
from django.conf import settings
import socketio

from conversations.socket_server import sio
from conversations.namespaces.utils import detect_platform_from_socketio_environ
from workflows.services.workflow_coordinator import WorkflowCoordinator
from workflows.services.workflow_run_service import get_user


logger = logging.getLogger(__name__)


class WorkflowNamespace(socketio.AsyncNamespace):
    """
    Socket.IO namespace for workflow execution streaming.

    Thin event handler that delegates to WorkflowCoordinator for all
    business logic. Maintains only connection state and session data.

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

        # Coordinator handles all business logic
        self.coordinator = WorkflowCoordinator(sio=sio, namespace='/workflow')

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
                logger.warning(
                    f"Workflow Socket.IO connect rejected: no auth provided (sid={sid})"
                )
                raise socketio.exceptions.ConnectionRefusedError('Authentication required')

            token = auth.get('token')
            if not token:
                logger.warning(
                    f"Workflow Socket.IO connect rejected: no token provided (sid={sid})"
                )
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
                raise socketio.exceptions.ConnectionRefusedError(
                    'Invalid token: missing user_id'
                )

            # Fetch user from database
            user = await get_user(user_id)
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

            logger.info(
                f"Workflow Socket.IO connected: user={user.id}, sid={sid}, platform={platform}"
            )
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
            logger.info(
                f"Workflow Socket.IO disconnected: user={user.id if user else 'None'}, sid={sid}"
            )

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

            return await self.coordinator.subscribe_workflow_run(
                sid=sid,
                run_id=run_id,
                user=session['user'],
                session=session
            )

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
            if run_id:
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

            return await self.coordinator.subscribe_workflow(
                sid=sid,
                workflow_id=workflow_id,
                user=session['user'],
                session=session
            )

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

            return await self.coordinator.start_execution(
                sid=sid,
                user=session['user'],
                session=session,
                workflow_run_id=data.get('workflowRunId'),
                workflow_id=data.get('workflowId'),
                user_input=data.get('userInput', '')
            )

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

            workflow_id = data.get('workflowId')
            step_node_id = data.get('stepNodeId')

            if not workflow_id or not step_node_id:
                return {'error': 'workflowId and stepNodeId are required'}

            return await self.coordinator.execute_single_step(
                sid=sid,
                user=session['user'],
                session=session,
                workflow_id=workflow_id,
                step_node_id=step_node_id,
                workflow_run_id=data.get('workflowRunId')
            )

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

            workflow_run_id = data.get('workflowRunId')
            node_id = data.get('nodeId')
            selected_route = data.get('selectedRoute')

            if not all([workflow_run_id, node_id, selected_route]):
                return {
                    'error': 'Missing required fields: workflowRunId, nodeId, selectedRoute'
                }

            return await self.coordinator.submit_validation(
                user=session['user'],
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                selected_route=selected_route,
                continue_execution=data.get('continueExecution', True)
            )

        except Exception as e:
            logger.exception(f"Submit validation error: {str(e)}")
            return {'error': str(e)}


# Create the namespace instance
workflow_namespace = WorkflowNamespace()
