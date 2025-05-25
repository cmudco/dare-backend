import json
import logging
import uuid
from typing import Optional, Dict, Any
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.exceptions import DenyConnection
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from pydantic import ValidationError
from asgiref.sync import sync_to_async
from djangorestframework_camel_case.util import camelize
import asyncio

from conversations.models import Conversation, Message, LLM
from core.services.conversation_service import ConversationService
from core.services.llm_service import LLMService
from core.services.billing_service import BillingService
from .constants import SenderType
from conversations.api.serializers import MessageSerializer

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 1024
    DEFAULT_MAX_CONTEXT_SNIPPETS = 4
    DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD = 0.5
    DEFAULT_HISTORY_LIMIT = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_service = ConversationService()
        self.llm_service = LLMService()
        self.billing_service = BillingService()
        self.user: Optional[User] = None
        self.conversation: Optional[Conversation] = None
        self.conversation_id: Optional[str] = None

    async def connect(self):
        """Initialize WebSocket connection and validate conversation."""
        try:
            self.user = self.scope["user"]
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")
            self.conversation = await self.conversation_service.get_conversation(self.conversation_id, self.user)
            if not self.conversation:
                logger.warning(f"Invalid conversation_id: {self.conversation_id} for user: {self.user.id}")
                raise DenyConnection("Invalid conversation_id")
            await self.accept()
            await self.load_conversation_history()
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
        except json.JSONDecodeError:
            await self.send_error("invalid_json", "Invalid JSON format")
        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")
            await self.send_error("processing_error", "Failed to process message")

    async def handle_new_message(self, data: Dict[str, Any]):
        """Process new user message and stream AI response."""
        try:
            message_data = self._validate_message_data(data)
            llm = await self._get_llm(message_data.get("llm_id"))
            if not await self.billing_service.check_sufficient_credits(self.user, llm):
                await self.send_error("insufficient_credits", "Insufficient wallet balance")
                return

            message_obj = await self.conversation_service.create_message(
                self.conversation,
                message_data["sender_type"],
                message_data["message"],
                self.user.email,
                message_data["file_ids"]
            )
            await self.send(await self._format_message(message_obj, is_sender=True))

            if await self.conversation_service.is_first_message(self.conversation):
                asyncio.create_task(self._generate_conversation_title(message_data["message"]))

            bot_message_obj = await self.conversation_service.create_message(
                self.conversation, SenderType.AI_ASSISTANT, "", "AI Assistant", llm=llm
            )
            await self.send(await self._format_message(bot_message_obj, streaming=True))

            await self._stream_ai_response(message_data, bot_message_obj, llm)
        except ValidationError as e:
            await self.send_error("validation_error", str(e))
        except Exception as e:
            logger.exception(f"Error in handle_new_message: {str(e)}")
            await self.send_error("ai_response_error", "Failed to generate AI response")

    async def handle_edit_message(self, data: Dict[str, Any]):
        """Edit the latest user message."""
        try:
            message_id = data.get("message_id")
            new_content = data.get("message", "").strip()
            if not message_id or not new_content:
                await self.send_error("missing_data", "Missing message_id or message content")
                return

            updated_message = await self.conversation_service.edit_message(
                message_id, new_content, self.conversation
            )
            await self.send(await self._format_message(updated_message, is_sender=True))
        except ValueError as e:
            await self.send_error("invalid_edit", str(e))
        except Exception as e:
            logger.exception(f"Error in handle_edit_message: {str(e)}")
            await self.send_error("edit_error", "Failed to edit message")

    async def handle_regenerate_response(self, data: Dict[str, Any]):
        """Regenerate an AI response for a given message."""
        try:
            message_id = data.get("message_id")
            if not message_id:
                await self.send_error("missing_data", "Missing message_id")
                return

            ai_message = await database_sync_to_async(
                lambda: Message.active_objects.select_related('llm').filter(
                    id=message_id, sender_type=SenderType.AI_ASSISTANT
                ).first()
            )()
            if not ai_message:
                await self.send_error("invalid_message", "AI message not found")
                return

            preceding_user_message = await self._get_preceding_user_message(ai_message)
            if not preceding_user_message:
                await self.send_error("no_user_message", "No preceding user message found")
                return

            llm = await self._get_llm(data.get("llm_id"), default=ai_message.llm)
            if not await self.billing_service.check_sufficient_credits(self.user, llm):
                await self.send_error("insufficient_credits", "Insufficient wallet balance")
                return

            message_data = self._validate_message_data(data, default_message=preceding_user_message.message)
            await self._stream_ai_response(message_data, ai_message, llm, regenerate=True)
        except Exception as e:
            logger.exception(f"Error in handle_regenerate_response: {str(e)}")
            await self.send_error("regenerate_error", "Failed to regenerate response")

    async def _stream_ai_response(self, message_data: Dict[str, Any], message_obj: Message, llm: LLM, regenerate: bool = False):
        """Stream AI response and handle billing."""
        try:
            bot_message_id = str(message_obj.id)
            ai_response_accumulator = ""
            token_usage = None

            async for chunk, usage in self.llm_service.query(
                message_data["message"],
                self.conversation,
                llm,
                message_data["file_ids"],
                message_data["tag_ids"],
                user_id=self.user.id,
                prompt_id=message_data["prompt_id"],
                temperature=message_data["temperature"],
                max_tokens=message_data["max_tokens"],
                max_context_snippets=message_data["max_context_snippets"],
                document_similarity_threshold=message_data["document_similarity_threshold"],
                history_limit=message_data["history_limit"],
                message_obj=message_obj
            ):
                if usage:
                    token_usage = usage
                    can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                        self.user, llm, token_usage
                    )
                    if not can_continue:
                        await self._handle_insufficient_balance(
                            message_obj, ai_response_accumulator, token_usage, error_response
                        )
                        return

                if chunk.strip():
                    ai_response_accumulator += chunk
                    payload = {
                        "type": "ai_stream",
                        "id": bot_message_id,
                        "message": ai_response_accumulator,
                        "senderName": "AI Assistant",
                        "senderType": SenderType.AI_ASSISTANT,
                        "isSender": False,
                        "streaming": True,
                        "regenerate": regenerate,
                        "date": message_obj.created_at.isoformat(),
                    }
                    await self.send(json.dumps(camelize(payload)))

            if ai_response_accumulator.strip():
                await self._finalize_message(message_obj, ai_response_accumulator, token_usage, regenerate)
        except Exception as e:
            logger.exception(f"Error streaming AI response: {str(e)}")
            await self.send_error("stream_error", "Failed to stream AI response")

    async def _finalize_message(self, message_obj: Message, ai_response: str, token_usage: Dict, regenerate: bool):
        """Finalize AI message with billing and send final response."""
        try:
            if regenerate and not message_obj.original_message:
                message_obj.original_message = message_obj.message
                await database_sync_to_async(message_obj.save)(update_fields=['original_message'])

            updated_message = await sync_to_async(
                self.conversation_service.finalize_ai_message_with_billing
            )(message_obj, ai_response, token_usage)
            if regenerate:
                updated_message.is_regenerated = True
                await database_sync_to_async(updated_message.save)(update_fields=['is_regenerated'])
            await self.send(await self._format_message(updated_message, streaming=False, regenerate=regenerate))
        except (ValidationError, DjangoValidationError) as e:
            logger.error(f"Validation error finalizing message: {str(e)}")
            await self.send_error("insufficient_balance", "Insufficient wallet balance", details={"error": str(e)})
        except Exception as e:
            logger.exception(f"Error finalizing message: {str(e)}")
            await self.send_error("finalize_error", "Failed to finalize message")

    async def _handle_insufficient_balance(self, message_obj: Message, ai_response: str, token_usage: Dict, error_response: Dict):
        """Handle insufficient balance during streaming."""
        message_obj.message = f"{ai_response}\n\n[Response cut off - insufficient credits]"
        message_obj.input_tokens = token_usage.get('input_tokens', 0)
        message_obj.output_tokens = token_usage.get('output_tokens', 0)
        await database_sync_to_async(message_obj.save)()
        await self.send(json.dumps(camelize({
            "type": "ai_stream",
            "id": str(message_obj.id),
            "message": message_obj.message,
            "senderName": "AI Assistant",
            "senderType": SenderType.AI_ASSISTANT,
            "isSender": False,
            "streaming": False,
            "regenerate": False,
            "date": message_obj.created_at.isoformat(),
        })))
        await self.send(json.dumps(error_response))

    async def load_conversation_history(self):
        """Fetches and sends conversation history to the frontend."""
        history = await self.conversation_service.fetch_chat_history_from_db(self.conversation)
        await self.send(json.dumps(camelize({"type": "conversation_history", "conversationHistory": history})))

    async def _generate_conversation_title(self, user_message: str):
        """Generate and send conversation title for the first message."""
        title = await self.conversation_service.generate_title(user_message)
        await self.conversation_service.update_conversation_title(self.conversation, title)
        await self.send(json.dumps(camelize({"type": "conversation_title", "title": title})))

    async def _format_message(self, message_obj: Message, is_sender: bool = False, streaming: bool = False, regenerate: bool = False):
        """Format message for WebSocket response."""
        serialized_data = await database_sync_to_async(lambda: MessageSerializer(message_obj).data)()
        llm_id = await database_sync_to_async(lambda: getattr(message_obj.llm, 'id', None))()
        response = {
            "type": "message",
            "id": str(message_obj.id),
            "message": message_obj.message,
            "senderType": message_obj.sender_type,
            "senderName": message_obj.sender or "AI Assistant",
            "isSender": is_sender,
            "streaming": streaming,
            "regenerate": regenerate,
            "date": message_obj.created_at.isoformat(),
            "llmId": llm_id,
            "snippets": serialized_data.get("snippets", []),
            "isEdited": message_obj.is_edited,
            "isRegenerated": message_obj.is_regenerated,
            "originalMessage": message_obj.original_message,
        }
        return json.dumps(camelize(response))

    async def _get_llm(self, llm_id: Optional[str], default: LLM = None) -> LLM:
        """Fetch LLM by ID or return default/first available."""
        if llm_id:
            llm = await database_sync_to_async(lambda: LLM.objects.filter(id=llm_id).first())()
            if llm:
                return llm
        return default or await database_sync_to_async(lambda: LLM.objects.first())()

    def _validate_message_data(self, data: Dict[str, Any], default_message: str = None) -> Dict[str, Any]:
        """Validate and extract message data."""
        return {
            "message": (data.get("message", default_message or "").strip()),
            "sender_type": data.get("sender_type", SenderType.PLAYER),
            "file_ids": data.get("file_ids", []),
            "tag_ids": data.get("tag_ids", []),
            "llm_id": data.get("llm_id"),
            "prompt_id": data.get("prompt_id"),
            "temperature": data.get("temperature", self.DEFAULT_TEMPERATURE),
            "max_tokens": data.get("max_tokens", self.DEFAULT_MAX_TOKENS),
            "max_context_snippets": data.get("max_context_snippets", self.DEFAULT_MAX_CONTEXT_SNIPPETS),
            "document_similarity_threshold": data.get("document_similarity_threshold", self.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD),
            "history_limit": data.get("history_limit", self.DEFAULT_HISTORY_LIMIT),
        }

    async def send_error(self, code: str, message: str, details: Dict = None):
        """Send standardized error response."""
        error_response = {"error": code, "message": message}
        if details:
            error_response["details"] = details
        await self.send(json.dumps(error_response))

    async def _get_preceding_user_message(self, ai_message: Message) -> Optional[Message]:
        """Retrieve the preceding user message."""
        preceding_messages = await database_sync_to_async(
            lambda: list(Message.active_objects.filter(
                conversation=self.conversation, created_at__lt=ai_message.created_at
            ).order_by('-created_at'))
        )()
        return next((msg for msg in preceding_messages if msg.sender_type == SenderType.PLAYER), None)