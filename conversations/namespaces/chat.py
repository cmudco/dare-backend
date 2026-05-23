"""
Chat Namespace for Socket.IO

Handles all real-time chat functionality including:
- User authentication via JWT (authenticated users)
- Session-based authentication (public/anonymous users)
- Conversation subscriptions (join/leave rooms)
- Message sending and streaming
- Artifact generation and control
- Learning progress (Socratic platform)

This namespace replaces the per-conversation ChatConsumer pattern
with a single persistent connection model.
"""

import json
import logging
import jwt
import base64
from typing import Dict, Any, Optional, Set
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from core.utils.db import db_reconnect_on_stale
import socketio

from conversations.socket_server import sio
from conversations.models import Conversation, Artifact
from conversations.constants import (
    ErrorCode,
    ErrorMessage,
    ArtifactStatus,
    DEFAULT_ANONYMOUS_USER_NAME,
)
from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.message_validation_service import MessageValidationService
from conversations.services.audio_transcription_service import AudioTranscriptionService
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.namespaces.utils import detect_platform_from_socketio_environ
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
    - Dual authentication: JWT (authenticated users) or session_id (public bots)
    """

    def __init__(self):
        super().__init__(namespace='/chat')
        self.conversation_service = ConversationService()

        # Session tracking: {sid: {'user': User|None, 'subscriptions': set(), 'platform': str, 'is_public': bool, 'session_id': str|None}}
        self.sessions: Dict[str, Dict[str, Any]] = {}

        # Active message coordinators: {f"{sid}_{conv_id}": MessageCoordinator}
        self.coordinators: Dict[str, MessageCoordinator] = {}

    # ==================== Connection Lifecycle ====================

    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None):
        """
        Handle new connection with JWT or session_id authentication.

        Supports two authentication modes:
        1. JWT Token (authenticated users): auth={'token': 'jwt_token'}
        2. Session ID (public bots): auth={'sessionId': 'uuid', 'conversationId': 'uuid'}

        Args:
            sid: Socket session ID
            environ: ASGI environ dict with request info
            auth: Client-provided auth data

        Returns:
            True if auth successful, raises ConnectionRefusedError otherwise
        """
        try:
            if not auth:
                logger.warning(f"Socket.IO connect rejected: no auth provided (sid={sid})")
                raise socketio.exceptions.ConnectionRefusedError('Authentication required')

            # Check which auth mode is being used
            token = auth.get('token')
            session_id = auth.get('sessionId')

            if token:
                # JWT authentication for authenticated users
                return await self._connect_with_jwt(sid, environ, token)
            elif session_id:
                # Session-based authentication for public bots
                conversation_id = auth.get('conversationId')
                if not conversation_id:
                    raise socketio.exceptions.ConnectionRefusedError('conversationId required for session auth')
                return await self._connect_with_session(sid, environ, session_id, conversation_id)
            else:
                logger.warning(f"Socket.IO connect rejected: no token or sessionId provided (sid={sid})")
                raise socketio.exceptions.ConnectionRefusedError('Authentication required: provide token or sessionId')

        except socketio.exceptions.ConnectionRefusedError:
            raise
        except Exception as e:
            logger.exception(f"Socket.IO connect error: {str(e)}")
            raise socketio.exceptions.ConnectionRefusedError(f'Connection failed: {str(e)}')

    async def _connect_with_jwt(self, sid: str, environ: dict, token: str) -> bool:
        """Handle JWT-based authentication for authenticated users."""
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
            'is_public': False,
            'session_id': None,
        }

        # Join user-specific room for direct notifications
        await sio.enter_room(sid, f'user_{user.id}', namespace='/chat')

        logger.info(f"Socket.IO connected (JWT): user={user.id}, sid={sid}, platform={platform}")
        return True

    async def _connect_with_session(self, sid: str, environ: dict, session_id: str, conversation_id: str) -> bool:
        """Handle session-based authentication for public bots."""
        # Get conversation and validate it belongs to this session
        conversation = await self.conversation_service.get_conversation_by_id(conversation_id)

        if not conversation:
            logger.warning(f"Invalid conversation_id for public bot: {conversation_id}")
            raise socketio.exceptions.ConnectionRefusedError('Invalid conversation')

        # Verify this conversation belongs to this anonymous session
        if conversation.anonymous_session_id != session_id:
            logger.warning(f"Session mismatch for conversation {conversation_id}")
            raise socketio.exceptions.ConnectionRefusedError('Invalid session for this conversation')

        # Verify conversation has no user (is public)
        if conversation.user is not None:
            logger.warning(f"Conversation {conversation_id} is not a public conversation")
            raise socketio.exceptions.ConnectionRefusedError('Not a public conversation')

        # Detect platform from Origin/Referer headers
        platform = detect_platform_from_socketio_environ(environ)

        # Store session data (user=None for public bots)
        self.sessions[sid] = {
            'user': None,
            'subscriptions': set(),
            'platform': platform,
            'is_public': True,
            'session_id': session_id,
            'conversation_id': conversation_id,  # Store for auto-subscribe
        }

        # Auto-subscribe to the conversation room (public bots connect directly to a conversation)
        room_name = f'conversation_{conversation_id}'
        await sio.enter_room(sid, room_name, namespace='/chat')
        self.sessions[sid]['subscriptions'].add(conversation_id)

        # Create coordinator for this conversation
        coordinator = MessageCoordinator(
            conversation=conversation,
            user=None,  # No user for public bots
            platform=platform,
            send_callback=self._create_send_callback(sid, conversation_id),
        )
        self.coordinators[f"{sid}_{conversation_id}"] = coordinator

        # Send conversation history
        await coordinator.send_conversation_history()

        # Send latest learning progress if applicable
        await coordinator.send_latest_learning_progress()

        logger.info(f"Socket.IO connected (session): conv_id={conversation_id}, sid={sid}, platform={platform}, bot_id={conversation.bot_id}")
        return True
    
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
            is_public = session.get('is_public', False)

            if is_public:
                logger.info(f"Socket.IO disconnected (public): session_id={session.get('session_id')}, sid={sid}")
            else:
                logger.info(f"Socket.IO disconnected (JWT): user={user.id if user else 'None'}, sid={sid}")
            
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

        Note: Public bot sessions are auto-subscribed on connect, so this is
        primarily for authenticated users switching between conversations.

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

            # Public bots are auto-subscribed on connect
            if session.get('is_public', False):
                return {'success': True, 'conversationId': session.get('conversation_id')}

            conv_id = data.get('conversationId')
            if not conv_id:
                return {'error': 'Missing conversationId'}

            user = session['user']
            # Platform is determined at connection time via Origin header (single source of truth)
            platform = session.get('platform', 'DARE')

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

            # Determine sender name based on auth type
            user = session.get('user')
            is_public = session.get('is_public', False)
            sender_name = DEFAULT_ANONYMOUS_USER_NAME if is_public else user.email

            # Handle message (async, doesn't block return)
            await coordinator.handle_new_message(
                message_data=message_data,
                sender_name=sender_name,
            )

            return {'success': True}

        except Exception as e:
            logger.exception(f"Send message error: {str(e)}")
            return {'error': str(e)}

    async def on_send_voice_message(self, sid: str, data: dict) -> dict:
        """
        Handle voice message input - transcribe audio and send as text message.

        This is a thin wrapper that:
        1. Decodes base64 audio
        2. Transcribes using AudioTranscriptionService
        3. Delegates to normal message flow

        Args:
            sid: Socket session ID
            data: {
                'conversationId': str,
                'audio': str (base64),
                'audioFormat': str ('webm', 'wav', etc.),
                'language': str (optional, default 'auto')
            }

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

            # Get the coordinator
            coordinator = self.coordinators.get(f"{sid}_{conv_id}")
            if not coordinator:
                return {'error': 'Conversation not initialized'}

            # Extract audio data
            audio_base64 = data.get('audio')
            audio_format = data.get('audioFormat', 'webm')
            language = data.get('language', 'auto')

            if not audio_base64:
                return {'error': 'Missing audio data'}

            # Send processing indicator
            await sio.emit('message', {
                'type': 'voice_processing',
                'status': 'transcribing',
            }, room=f'conversation_{conv_id}', namespace='/chat')

            # Transcribe audio

            try:
                audio_bytes = base64.b64decode(audio_base64)
            except Exception as e:
                logger.error(f"Failed to decode audio: {str(e)}")
                return {'error': 'Invalid audio data'}

            # Transcribe the audio bytes
            language_param = None if language == 'auto' else language
            transcribed_text = await AudioTranscriptionService.transcribe_audio_bytes(
                audio_bytes=audio_bytes,
                audio_format=audio_format,
                language=language_param,
            )

            if not transcribed_text or not transcribed_text.strip():
                await sio.emit('message', {
                    'type': 'voice_transcription',
                    'status': 'error',
                    'error': 'Could not transcribe audio. Please try again.',
                }, room=f'conversation_{conv_id}', namespace='/chat')
                return {'error': 'Transcription returned empty result'}

            # Send transcription back to frontend (user can review/edit before sending)
            await sio.emit('message', {
                'type': 'voice_transcription',
                'status': 'complete',
                'text': transcribed_text.strip(),
            }, room=f'conversation_{conv_id}', namespace='/chat')

            logger.info(f"Voice transcribed: conv_id={conv_id}, chars={len(transcribed_text)}")
            return {'success': True, 'text': transcribed_text.strip()}

        except Exception as e:
            logger.exception(f"Send voice message error: {str(e)}")
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
            try:
                data = json.loads(message) if isinstance(message, str) else message
                await sio.emit('message', data, room=f'conversation_{conv_id}', namespace='/chat')
            except Exception as e:
                logger.debug(f"Send callback failed (client may have disconnected): {e}")
        
        return send_callback
    
    @sync_to_async
    def _get_user(self, user_id: int):
        """Fetch user by ID; reconnects once if the thread-local connection is stale."""
        try:
            return db_reconnect_on_stale(User.objects.get, id=user_id)
        except User.DoesNotExist:
            return None

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
