import json
import logging
from typing import Optional, Dict, Any
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.exceptions import DenyConnection
from django.contrib.auth import get_user_model
from pydantic import ValidationError

from conversations.models import Conversation
from core.services.conversation_service import ConversationService
from conversations.services.message_validation_service import MessageValidationService
from conversations.services.websocket_response_service import WebSocketResponseService
from conversations.services.message_coordinator import MessageCoordinator
from .constants import (
    DEFAULT_ANONYMOUS_USER_NAME,
    ErrorCode,
    ErrorMessage,
)
from users.utils import detect_platform_from_scope

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for authenticated user chat conversations.

    Handles real-time messaging between users and AI assistants with:
    - Message creation and editing
    - AI response streaming
    - Learning progress tracking (Socratic platform)
    - Billing integration
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_service = ConversationService()
        self.user: Optional[User] = None
        self.conversation: Optional[Conversation] = None
        self.conversation_id: Optional[str] = None
        self.platform: Optional[str] = None
        self.coordinator: Optional[MessageCoordinator] = None

    async def connect(self):
        """Initialize WebSocket connection and validate conversation."""
        try:
            self.user = self.scope["user"]
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
            self.conversation = await self.conversation_service.get_conversation(self.conversation_id, self.user)
            if not self.conversation:
                logger.warning(f"Invalid conversation_id: {self.conversation_id} for user: {self.user.id}")
                raise DenyConnection(ErrorMessage.INVALID_CONVERSATION)
            # Detect platform from ASGI scope headers
            self.platform = detect_platform_from_scope(self.scope)

            # Initialize MessageCoordinator with conversation context
            self.coordinator = MessageCoordinator(
                conversation=self.conversation,
                user=self.user,
                platform=self.platform,
                send_callback=self.send,
            )

            logger.info(f"WebSocket connected: user={self.user.id}, conversation={self.conversation_id}, platform={self.platform}")
            await self.accept()

            # Load conversation history using coordinator
            await self.coordinator.send_conversation_history()

            # Also send the latest learning progress assessment if available
            await self.coordinator.send_latest_learning_progress()
        except DenyConnection as e:
            logger.error(f"Connection denied: {str(e)}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Error during connect: {str(e)}")
            await self.close(code=4001)

    async def receive(self, text_data: str = None, bytes_data: bytes = None):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            action = data.get("action")

            if action == "edit_message":
                await self.handle_edit_message(data)
            elif action == "regenerate_response":
                await self.handle_regenerate_response(data)
            else:
                await self.handle_new_message(data)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            await self.send_error(ErrorCode.INVALID_JSON, ErrorMessage.INVALID_JSON)
        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")
            await self.send_error(ErrorCode.PROCESSING_ERROR, ErrorMessage.PROCESSING_ERROR)

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process new user message and stream AI response using MessageCoordinator."""
        try:
            # Validate message data
            message_data = self._validate_message_data(data)

            # Delegate to MessageCoordinator which handles:
            # - Billing checks
            # - Message creation
            # - AI response streaming
            # - Learning progress (if enabled)
            # - Conversation title generation
            await self.coordinator.handle_new_message(
                message_data=message_data,
                sender_name=self.user.email,
            )
        except ValidationError as e:
            await self.send_error(ErrorCode.VALIDATION_ERROR, str(e))
        except Exception as e:
            logger.exception(f"Error in handle_new_message: {str(e)}")
            await self.send_error(ErrorCode.AI_RESPONSE_ERROR, ErrorMessage.AI_RESPONSE_ERROR)

    async def handle_edit_message(self, data: Dict[str, Any]):
        """Edit the latest user message."""
        try:
            message_id = data.get("message_id")
            new_content = data.get("message", "").strip()
            if not message_id or not new_content:
                await self.send_error(ErrorCode.MISSING_DATA, ErrorMessage.MISSING_MESSAGE_CONTENT)
                return

            updated_message = await self.conversation_service.edit_message(
                message_id, new_content, self.conversation
            )

            # Format and send the updated message
            formatted_message = await WebSocketResponseService.format_message(
                message=updated_message,
                message_type="message",
                is_sender=True,
                streaming=False,
                regenerate=False
            )
            await self.send(json.dumps(formatted_message))
        except ValueError as e:
            await self.send_error(ErrorCode.INVALID_EDIT, str(e))
        except Exception as e:
            logger.exception(f"Error in handle_edit_message: {str(e)}")
            await self.send_error(ErrorCode.EDIT_ERROR, ErrorMessage.EDIT_ERROR)

    async def handle_regenerate_response(self, data: Dict[str, Any]):
        """Regenerate an AI response using MessageCoordinator."""
        try:
            # Validate message data
            message_data = self._validate_message_data(data, default_message="")

            # Delegate to MessageCoordinator which handles:
            # - Finding the preceding user message
            # - Billing checks
            # - Reusing existing AI message
            # - Streaming regenerated response
            # - Marking message as regenerated
            await self.coordinator.handle_regenerate_response(
                message_data=message_data,
            )
        except Exception as e:
            logger.exception(f"Error in handle_regenerate_response: {str(e)}")
            await self.send_error(ErrorCode.REGENERATE_ERROR, ErrorMessage.REGENERATE_ERROR)


    def _validate_message_data(self, data: Dict[str, Any], default_message: str = None) -> Dict[str, Any]:
        """Validate and extract message data using MessageValidationService."""
        return MessageValidationService.validate_and_parse(data, default_message)

    async def send_error(self, code: str, message: str, details: Dict = None):
        """Send standardized error response using WebSocketResponseService."""
        error_response = WebSocketResponseService.format_error(code, message, details)
        await self.send(json.dumps(error_response))


class PublicBotConsumer(ChatConsumer):
    """
    WebSocket consumer for public bot conversations (no user authentication required).
    Uses anonymous_session_id instead of user for validation.
    Inherits chat functionality from ChatConsumer and uses MessageCoordinator.
    """

    async def connect(self):
        """Initialize WebSocket connection for public bot (no auth required)."""
        try:
            # Get conversation_id from URL
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")

            # Get session_id from query string
            query_string = self.scope.get('query_string', b'').decode()
            params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
            session_id = params.get('session_id')

            if not session_id:
                logger.warning(f"No session_id provided for public bot conversation {self.conversation_id}")
                raise DenyConnection("session_id is required")

            # Get conversation and validate it belongs to this session
            self.conversation = await self.conversation_service.get_conversation_by_id(self.conversation_id)

            if not self.conversation:
                logger.warning(f"Invalid conversation_id: {self.conversation_id}")
                raise DenyConnection(ErrorMessage.INVALID_CONVERSATION)

            # Verify this conversation belongs to this anonymous session
            if self.conversation.anonymous_session_id != session_id:
                logger.warning(f"Session mismatch for conversation {self.conversation_id}")
                raise DenyConnection("Invalid session for this conversation")

            # Verify conversation has no user (is public)
            if self.conversation.user is not None:
                logger.warning(f"Conversation {self.conversation_id} is not a public conversation")
                raise DenyConnection("Not a public conversation")

            # Set user to None for public conversations
            self.user = None

            # Detect platform from ASGI scope headers
            self.platform = detect_platform_from_scope(self.scope)

            # Initialize MessageCoordinator for public bot (user=None)
            self.coordinator = MessageCoordinator(
                conversation=self.conversation,
                user=None,  # No user for public bots
                platform=self.platform,
                send_callback=self.send,
            )

            logger.info(f"Public bot WebSocket connected: conversation={self.conversation_id}, platform={self.platform}, bot_id={self.conversation.bot_id}")
            await self.accept()

            # Load conversation history using coordinator
            await self.coordinator.send_conversation_history()

            # Also send the latest learning progress assessment if available
            await self.coordinator.send_latest_learning_progress()

        except DenyConnection as e:
            logger.error(f"Public bot connection denied: {str(e)}")
            await self.close(code=4000)
        except Exception as e:
            logger.exception(f"Error during public bot connect: {str(e)}")
            await self.close(code=4001)

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process new user message for public bot using MessageCoordinator."""
        try:
            # Validate message data
            message_data = self._validate_message_data(data)

            # Delegate to MessageCoordinator which handles:
            # - Message creation (no billing for public bots)
            # - AI response streaming
            # - Bot budget tracking
            # - Learning progress (if enabled)
            # - Conversation title generation
            await self.coordinator.handle_new_message(
                message_data=message_data,
                sender_name=DEFAULT_ANONYMOUS_USER_NAME,
            )
        except ValidationError as e:
            await self.send_error(ErrorCode.VALIDATION_ERROR, str(e))
        except Exception as e:
            logger.exception(f"Error in handle_new_message (public): {str(e)}")
            await self.send_error(ErrorCode.AI_RESPONSE_ERROR, ErrorMessage.AI_RESPONSE_ERROR)

    async def handle_regenerate_response(self, data: Dict[str, Any]):
        """Regenerate an AI response for public bot using MessageCoordinator."""
        try:
            # Validate message data
            message_data = self._validate_message_data(data, default_message="")

            # Delegate to MessageCoordinator
            await self.coordinator.handle_regenerate_response(
                message_data=message_data,
            )
        except Exception as e:
            logger.exception(f"Error in handle_regenerate_response (public): {str(e)}")
            await self.send_error(ErrorCode.REGENERATE_ERROR, ErrorMessage.REGENERATE_ERROR)