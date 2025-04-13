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
from channels.db import database_sync_to_async
import asyncio
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize  # Import camelize

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
        """Handle WebSocket disconnection."""
        await self.close()

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming WebSocket messages, process them, and stream AI responses.
        """
        try:
            data = json.loads(text_data)
            msg_content = data.get("message", "").strip()
            sender_type = data.get("sender_type", SenderType.PLAYER)
            file_ids = data.get("file_ids", [])
            tag_ids = data.get("tag_ids", [])
            llm_id = data.get("llm_id")
            prompt_id = data.get("prompt_id")
            temperature = data.get("temperature", 0.7)
            max_tokens = data.get("max_tokens", 2048)
            max_context_snippets = data.get("max_context_snippets", 4)
            document_similarity_threshold = data.get("document_similarity_threshold", 0.5)

            message_obj = await self.conversation_service.create_message(
                self.conversation, sender_type, msg_content, self.user.email, file_ids
            )
            await self.send(await self.format_message(message_obj, is_sender=True))

            asyncio.create_task(self.handle_title_generation(msg_content))

            llm = await database_sync_to_async(LLM.objects.filter(id=llm_id).first)() if llm_id else await database_sync_to_async(LLM.objects.first)()

            bot_message_obj = await self.conversation_service.create_message(
                self.conversation, SenderType.AI_ASSISTANT, "", "AI Assistant", [], llm=llm
            )
            await self.send(await self.format_message(bot_message_obj, streaming=True))

            await self.handle_ai_response(
                msg_content,
                bot_message_obj,
                llm,
                file_ids,
                tag_ids=tag_ids,
                prompt_id=prompt_id,
                temperature=temperature,
                max_tokens=max_tokens,
                max_context_snippets=max_context_snippets,
                document_similarity_threshold=document_similarity_threshold
            )

        except Exception as e:
            logger.exception(f"Error processing message: {str(e)}")

    async def handle_title_generation(self, user_message):
        is_first_message = await self.conversation_service.is_first_message(self.conversation)
        if is_first_message:
            title = await self.conversation_service.generate_title(user_message, "")
            await self.conversation_service.update_conversation_title(self.conversation, title)
            payload = {"type": "conversation_title", "title": title}
            await self.send(json.dumps(camelize(payload)))

    async def handle_ai_response(
        self,
        msg_content,
        bot_message_obj,
        llm,
        file_ids,
        tag_ids=None,
        prompt_id=None,
        temperature=0.7,
        max_tokens=1024,
        max_context_snippets=4,
        document_similarity_threshold=0.5
    ):
        """Handles AI response streaming and updates the message."""
        bot_message_id = str(bot_message_obj.id)
        ai_response_accumulator = ""

        async for chunk in self.llm_service.query(
            msg_content,
            self.conversation,
            llm,
            file_ids,
            tag_ids,
            self.user.id,
            prompt_id,
            temperature,
            max_tokens,
            max_context_snippets,
            document_similarity_threshold,
            message_obj=bot_message_obj
        ):
            if chunk.strip():
                ai_response_accumulator += chunk
                payload = {
                    "type": "ai_stream",
                    "id": bot_message_id,
                    "message": chunk,
                    "senderName": "AI Assistant",
                    "senderType": SenderType.AI_ASSISTANT,
                    "isSender": False,
                    "streaming": True,
                    "date": bot_message_obj.created_at.isoformat(),
                }
                await self.send(json.dumps(camelize(payload)))

        if ai_response_accumulator.strip():
            await self.conversation_service.update_message(bot_message_id, ai_response_accumulator)
            bot_message_obj = await database_sync_to_async(
                lambda: Message.active_objects.prefetch_related('snippets').get(id=bot_message_obj.id)
            )()
            await self.send(await self.format_message(bot_message_obj, message=ai_response_accumulator, streaming=False))

    async def load_conversation_history(self, conversation):
        """Fetches chat history and sends it to the frontend."""
        conversation_history = await self.conversation_service.fetch_chat_history_from_db(conversation)
        payload = {
            "type": "conversation_history",
            "conversationHistory": conversation_history
        }
        await self.send(text_data=json.dumps(camelize(payload)))

    async def get_llm_id(self, message_obj):
        """Safely fetch the llm_id for a message object."""
        return await database_sync_to_async(lambda: getattr(message_obj.llm, 'id', None))()

    async def format_message(self, message_obj, message=None, is_sender=False, streaming=False):
        """Helper function to format message JSON response using the serializer."""
        serialized_data = await database_sync_to_async(lambda: MessageSerializer(message_obj).data)()
        llm_id = await self.get_llm_id(message_obj)

        response = {
            "type": "message",
            "id": str(message_obj.id),
            "message": message or message_obj.message,
            "senderType": message_obj.sender_type,
            "senderName": message_obj.sender or "AI Assistant",
            "isSender": is_sender,
            "streaming": streaming,
            "date": message_obj.created_at.isoformat(),
            "llmId": llm_id,
            "snippets": serialized_data.get("snippets", [])
        }
        return json.dumps(camelize(response))