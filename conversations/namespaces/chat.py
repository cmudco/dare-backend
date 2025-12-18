"""
Chat Namespace for Socket.IO

Handles all real-time chat functionality including:
- User authentication via JWT
- Conversation subscriptions (join/leave rooms)
- Message sending and streaming
- Artifact generation and control
- Learning progress (Socratic platform)

This namespace replaces the per-conversation ChatConsumer pattern
with a single persistent connection model.
"""

import logging
import jwt
from typing import Dict, Any, Optional, Set
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
import socketio

from conversations.socket_server import sio
from conversations.models import Conversation, Artifact
from conversations.constants import (
    ErrorCode,
    ErrorMessage,
    ArtifactStatus,
)
from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.message_validation_service import MessageValidationService
from core.services.conversation_service import ConversationService

User = get_user_model()
logger = logging.getLogger(__name__)


class ChatNamespace(socketio.AsyncNamespace):
    """
    Socket.IO namespace for chat functionality.
    
    Key features:
    - Single connection per user (not per conversation)
    - Event-based room subscriptions
    - Built-in heartbeat via Socket.IO engine
    - Automatic reconnection support
    """
    
    def __init__(self):
        super().__init__(namespace='/chat')
        self.conversation_service = ConversationService()
        
        # Session tracking: {sid: {'user': User, 'subscriptions': set(), 'platform': str}}
        self.sessions: Dict[str, Dict[str, Any]] = {}
        
        # Active message coordinators: {f"{sid}_{conv_id}": MessageCoordinator}
        self.coordinators: Dict[str, MessageCoordinator] = {}
    
    # ==================== Connection Lifecycle ====================
    
    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None):
        """
        Handle new connection with JWT authentication.
        
        Args:
            sid: Socket session ID
            environ: ASGI environ dict with request info
            auth: Client-provided auth data (expect {'token': 'jwt_token'})
        
        Returns:
            True if auth successful, raises ConnectionRefusedError otherwise
        """
        try:
            # Extract JWT token
            token = auth.get('token') if auth else None
            if not token:
                logger.warning(f"Socket.IO connect rejected: no token provided (sid={sid})")
                raise socketio.exceptions.ConnectionRefusedError('Authentication required')
            
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
            
            # Detect platform from headers (if available)
            platform = self._detect_platform_from_environ(environ)
            
            # Store session data
            self.sessions[sid] = {
                'user': user,
                'subscriptions': set(),
                'platform': platform,
            }
            
            # Join user-specific room for direct notifications
            await sio.enter_room(sid, f'user_{user.id}', namespace='/chat')
            
            logger.info(f"Socket.IO connected: user={user.id}, sid={sid}, platform={platform}")
            return True
            
        except socketio.exceptions.ConnectionRefusedError:
            raise
        except Exception as e:
            logger.exception(f"Socket.IO connect error: {str(e)}")
            raise socketio.exceptions.ConnectionRefusedError(f'Connection failed: {str(e)}')
    
    async def on_disconnect(self, sid: str):
        """
        Handle disconnection - cleanup session and pause artifacts.
        
        Args:
            sid: Socket session ID
        """
        try:
            session = self.sessions.pop(sid, None)
            if not session:
                return
            
            user = session.get('user')
            subscriptions = session.get('subscriptions', set())
            
            logger.info(f"Socket.IO disconnected: user={user.id if user else 'None'}, sid={sid}")
            
            # Pause any in-progress artifacts for subscribed conversations
            for conv_id in subscriptions:
                await self._pause_conversation_artifacts(conv_id)
                
                # Clean up coordinator
                coordinator_key = f"{sid}_{conv_id}"
                self.coordinators.pop(coordinator_key, None)
                
        except Exception as e:
            logger.exception(f"Socket.IO disconnect error: {str(e)}")
    
    # ==================== Subscription Events ====================
    
    async def on_subscribe_conversation(self, sid: str, data: dict) -> dict:
        """
        Subscribe to a conversation room to receive updates.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid'}
        
        Returns:
            {'success': True, 'conversationId': 'uuid'} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            if not conv_id:
                return {'error': 'Missing conversationId'}
            
            user = session['user']
            platform = data.get('platform') or session.get('platform', 'DARE')
            
            # Validate user has access to this conversation
            conversation = await self.conversation_service.get_conversation(conv_id, user)
            if not conversation:
                return {'error': ErrorMessage.INVALID_CONVERSATION}
            
            # Join the conversation room
            room_name = f'conversation_{conv_id}'
            await sio.enter_room(sid, room_name, namespace='/chat')
            session['subscriptions'].add(conv_id)
            
            # Create coordinator for this conversation
            coordinator = MessageCoordinator(
                conversation=conversation,
                user=user,
                platform=platform,
                send_callback=self._create_send_callback(sid, conv_id),
            )
            self.coordinators[f"{sid}_{conv_id}"] = coordinator
            
            # Send conversation history
            await coordinator.send_conversation_history()
            
            # Send latest learning progress if applicable
            await coordinator.send_latest_learning_progress()
            
            logger.info(f"Subscribed to conversation: user={user.id}, conv_id={conv_id}")
            return {'success': True, 'conversationId': conv_id}
            
        except Exception as e:
            logger.exception(f"Subscribe error: {str(e)}")
            return {'error': str(e)}
    
    async def on_unsubscribe_conversation(self, sid: str, data: dict) -> dict:
        """
        Unsubscribe from a conversation room.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid'}
        
        Returns:
            {'success': True}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            if not conv_id:
                return {'success': True}  # Nothing to unsubscribe
            
            # Leave the room
            room_name = f'conversation_{conv_id}'
            await sio.leave_room(sid, room_name, namespace='/chat')
            session['subscriptions'].discard(conv_id)
            
            # Pause any in-progress artifacts
            await self._pause_conversation_artifacts(conv_id)
            
            # Clean up coordinator
            coordinator_key = f"{sid}_{conv_id}"
            self.coordinators.pop(coordinator_key, None)
            
            logger.info(f"Unsubscribed from conversation: conv_id={conv_id}")
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Unsubscribe error: {str(e)}")
            return {'error': str(e)}
    
    # ==================== Message Events ====================
    
    async def on_send_message(self, sid: str, data: dict) -> dict:
        """
        Handle new message in a conversation.
        
        Args:
            sid: Socket session ID
            data: Message payload with conversationId
        
        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            if not conv_id or conv_id not in session['subscriptions']:
                return {'error': 'Not subscribed to this conversation'}
            
            # Get or create coordinator
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                return {'error': 'Conversation not initialized'}
            
            # Validate message data
            try:
                message_data = MessageValidationService.validate_and_parse(data)
            except Exception as e:
                return {'error': f'Validation error: {str(e)}'}
            
            # Handle message (async, doesn't block return)
            await coordinator.handle_new_message(
                message_data=message_data,
                sender_name=session['user'].email,
            )
            
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Send message error: {str(e)}")
            return {'error': str(e)}
    
    async def on_edit_message(self, sid: str, data: dict) -> dict:
        """
        Edit an existing message.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid', 'messageId': 'id', 'message': 'new content'}
        
        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            message_id = data.get('messageId')
            new_content = data.get('message', '').strip()
            
            if not conv_id or conv_id not in session['subscriptions']:
                return {'error': 'Not subscribed to this conversation'}
            
            if not message_id or not new_content:
                return {'error': 'Missing messageId or message content'}
            
            # Get coordinator
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                return {'error': 'Conversation not initialized'}
            
            # Edit message through conversation service
            conversation = coordinator.conversation
            updated_message = await coordinator.conversation_service.edit_message(
                message_id, new_content, conversation
            )
            
            # Broadcast update to all subscribers
            from conversations.services.websocket_response_service import WebSocketResponseService
            formatted = await WebSocketResponseService.format_message(
                message=updated_message,
                message_type="edit_message",
                is_sender=True,
                streaming=False,
            )
            await sio.emit('message', formatted, room=f'conversation_{conv_id}', namespace='/chat')
            
            return {'success': True}
            
        except ValueError as e:
            return {'error': str(e)}
        except Exception as e:
            logger.exception(f"Edit message error: {str(e)}")
            return {'error': str(e)}
    
    async def on_regenerate_response(self, sid: str, data: dict) -> dict:
        """
        Regenerate an AI response.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid', 'messageId': 'id', ...config}
        
        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            if not conv_id or conv_id not in session['subscriptions']:
                return {'error': 'Not subscribed to this conversation'}
            
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                return {'error': 'Conversation not initialized'}
            
            # Validate message data
            message_data = MessageValidationService.validate_and_parse(data, default_message="")
            
            await coordinator.handle_regenerate_response(message_data=message_data)
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Regenerate response error: {str(e)}")
            return {'error': str(e)}
    
    # ==================== Artifact Events ====================
    
    async def on_continue_artifact(self, sid: str, data: dict) -> dict:
        """
        Continue a paused artifact generation.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid', 'artifactId': 'id'}
        
        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            if not conv_id or conv_id not in session['subscriptions']:
                return {'error': 'Not subscribed to this conversation'}
            
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                return {'error': 'Conversation not initialized'}
            
            message_data = MessageValidationService.validate_and_parse(
                data, default_message="Continue generating"
            )
            
            await coordinator.handle_continue_artifact(message_data=message_data)
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Continue artifact error: {str(e)}")
            return {'error': str(e)}
    
    async def on_pause_artifact(self, sid: str, data: dict) -> dict:
        """
        Pause an in-progress artifact generation.
        
        Args:
            sid: Socket session ID
            data: {'conversationId': 'uuid', 'artifactId': 'id'}
        
        Returns:
            {'success': True} or {'error': 'message'}
        """
        try:
            session = self.sessions.get(sid)
            if not session:
                return {'error': 'Not authenticated'}
            
            conv_id = data.get('conversationId')
            artifact_id = data.get('artifactId')
            
            if not artifact_id:
                return {'error': 'Missing artifactId'}
            
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                # Still try to pause via direct query
                await self._pause_artifact_by_id(artifact_id)
                return {'success': True}
            
            await coordinator.handle_pause_artifact(artifact_id=artifact_id)
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Pause artifact error: {str(e)}")
            return {'error': str(e)}
    
    # ==================== Helper Methods ====================
    
    def _create_send_callback(self, sid: str, conv_id: str):
        """
        Create a send callback for MessageCoordinator.
        
        Messages are emitted to the conversation room so all
        subscribers receive them.
        """
        async def send_callback(message: str):
            import json
            try:
                data = json.loads(message) if isinstance(message, str) else message
                await sio.emit('message', data, room=f'conversation_{conv_id}', namespace='/chat')
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
    
    def _detect_platform_from_environ(self, environ: dict) -> str:
        """Detect platform from HTTP headers."""
        headers = dict(environ.get('asgi.http_headers', []))
        platform_header = headers.get(b'x-platform', b'').decode()
        return platform_header if platform_header in ('DARE', 'SocraticBots') else 'DARE'
    
    async def _pause_conversation_artifacts(self, conv_id: str):
        """Pause all in-progress artifacts for a conversation."""
        @sync_to_async
        def pause_artifacts():
            return Artifact.active_objects.filter(
                conversation__conversation_id=conv_id,
                status__in=[ArtifactStatus.PLANNING, ArtifactStatus.GENERATING]
            ).update(status=ArtifactStatus.PAUSED)
        
        try:
            count = await pause_artifacts()
            if count > 0:
                logger.info(f"Paused {count} artifact(s) for conversation {conv_id}")
        except Exception as e:
            logger.exception(f"Error pausing artifacts: {str(e)}")
    
    async def _pause_artifact_by_id(self, artifact_id: str):
        """Pause a specific artifact by ID."""
        @sync_to_async
        def pause_artifact():
            return Artifact.active_objects.filter(
                id=artifact_id,
                status__in=[ArtifactStatus.PLANNING, ArtifactStatus.GENERATING]
            ).update(status=ArtifactStatus.PAUSED)
        
        try:
            await pause_artifact()
        except Exception as e:
            logger.exception(f"Error pausing artifact {artifact_id}: {str(e)}")


# Register the namespace with the server
chat_namespace = ChatNamespace()
