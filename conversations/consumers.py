import logging
from channels.generic.websocket import AsyncWebsocketConsumer
import json
from urllib.parse import parse_qs
from asgiref.sync import sync_to_async
from django.conf import settings
from channels.exceptions import DenyConnection

from conversations.models import Conversation, Message, LLM
from core.services.conversation_service import ConversationService
from core.services.llm_service import LLMService
from .constants import SenderType
from django.contrib.auth import get_user_model
from files.models import File

import asyncio

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_service = ConversationService()
        self.llm_service = LLMService()

    async def connect(self):
        """Handles WebSocket connection and initializes conversation."""
        try:
            self.user = self.scope["user"]
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")

            conversation = await self.conversation_service.get_conversation(self.conversation_id, self.user)
            if not conversation:
                raise DenyConnection("Invalid conversation_id.")

            self.conversation = conversation

            await self.accept()
            await self.load_conversation_history(conversation)

        except DenyConnection:
            await self.close()
        except Exception as e:
            logger.exception(f"Error in WebSocket connection: {e}")
            await self.close()

    async def disconnect(self, close_code):
        self.close()

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming WebSocket messages, process them, and stream AI responses.
        """
        try:
            data = json.loads(text_data)
            msg_content = data.get("message", "").strip()
            sender_type = data.get("sender_type", SenderType.PLAYER)
            file_ids = data.get("file_ids", [])
            model_id = data.get("model_id")
            prompt = data.get("prompt")

            message_obj = await self.conversation_service.create_message(
                self.conversation, sender_type, msg_content, self.user.email, file_ids
            )
            await self.send(self.format_message(message_obj, is_sender=True))

            asyncio.create_task(self.handle_title_generation(msg_content))

            bot_message_obj = await self.conversation_service.create_message(
                self.conversation, SenderType.AI_ASSISTANT, "", "AI Assistant", []
            )
            await self.send(self.format_message(bot_message_obj, streaming=True))

            await self.handle_ai_response(msg_content, bot_message_obj, model_id, file_ids, prompt)

        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")

    async def handle_title_generation(self, user_message):
        """Generate and send the conversation title only if it's the first message."""
        is_first_message = await self.conversation_service.is_first_message(self.conversation)
        if is_first_message:
            title = await self.conversation_service.generate_title(user_message, "")
            await self.conversation_service.update_conversation_title(self.conversation, title)
            await self.send(json.dumps({"type": "conversation_title", "title": title}))

    async def handle_ai_response(self, msg_content, bot_message_obj, model_id, file_ids, prompt=None):
        """Handles AI response streaming and updates the message."""
        bot_message_id = str(bot_message_obj.id)
        ai_response_accumulator = ""

        async for chunk in self.llm_service.query(msg_content, self.conversation,model_id, file_ids, self.user.id, prompt):
            if chunk.strip():
                ai_response_accumulator += chunk
                await self.send(json.dumps({
                    "type": "ai_stream",
                    "id": bot_message_id,
                    "message": chunk,
                    "senderName": "AI Assistant",
                    "senderType": SenderType.AI_ASSISTANT,
                    "isSender": False,
                    "streaming": True,
                    "date": bot_message_obj.created_at.isoformat(),
                }))

        if ai_response_accumulator.strip():
            await self.conversation_service.update_message(bot_message_id, ai_response_accumulator)
            await self.send(self.format_message(bot_message_obj, message=ai_response_accumulator, streaming=False))

    async def load_conversation_history(self, conversation):
        """Fetches chat history and sends it to the frontend."""
        conversation_history = await self.conversation_service.fetch_chat_history_from_db(conversation)

        await self.send(text_data=json.dumps({
            "type": "conversation_history",
            "conversationHistory": conversation_history
        }))

    def format_message(self, message_obj, message=None, is_sender=False, streaming=False):
        """Helper function to format message JSON response."""
        return json.dumps({
            "type": "message",
            "id": str(message_obj.id),
            "message": message or message_obj.message,
            "senderType": message_obj.sender_type,
            "senderName": message_obj.sender or "AI Assistant",
            "isSender": is_sender,
            "streaming": streaming,
            "date": message_obj.created_at.isoformat(),
        })