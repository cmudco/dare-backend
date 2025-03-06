from channels.generic.websocket import AsyncWebsocketConsumer
import json
from urllib.parse import parse_qs
from asgiref.sync import sync_to_async
from django.conf import settings
from channels.exceptions import DenyConnection

from chats.models import Conversation, Message, LLM
from core.services.llm_service import LLMService
from .constants import SenderType
from django.contrib.auth import get_user_model
from files.models import File

from channels.db import database_sync_to_async

User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Handles WebSocket connection and initializes conversation."""
        try:
            self.user = self.scope["user"]
            self.conversation_id = self.scope["url_route"]["kwargs"].get("conversation_id")

            conversation = await self.get_conversation(self.conversation_id, self.user)
            if not conversation:
                raise DenyConnection("Invalid conversation_id.")

            self.conversation = conversation

            await self.accept()
            await self.load_chat_history(conversation)

        except DenyConnection:
            await self.close()
        except Exception as e:
            print(f"Error in WebSocket connection: {e}")
            await self.close()

    async def disconnect(self, close_code):
        if hasattr(self, 'conversation_id') and self.conversation_id:
            await self.channel_layer.group_discard(self.conversation_id, self.channel_name)

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

            message_obj = await self.create_message(
                self.conversation, sender_type, msg_content, self.user.email, file_ids
            )
            message_id = str(message_obj.id)

            await self.send(json.dumps({
                "id": message_id,
                "message": msg_content,
                "sender": self.user.email,
                "sender_type": sender_type,
                "files": file_ids,
                "is_sender": True,
            }))

            bot_message_obj = await self.create_message(
                self.conversation, SenderType.AI_ASSISTANT, "", "AI Assistant", []
            )
            bot_message_id = str(bot_message_obj.id)

            await self.send(json.dumps({
                "id": bot_message_id,
                "message": "",
                "sender": "AI Assistant",
                "sender_type": SenderType.AI_ASSISTANT,
                "streaming": True,
                "is_sender": False,
            }))

            llm_service = LLMService()
            ai_response_accumulator = ""

            async for chunk in llm_service.query(msg_content, self.conversation, model_id, file_ids, self.user.id):
                if not chunk.strip():
                    continue

                ai_response_accumulator += chunk

                await self.send(json.dumps({
                    "id": bot_message_id,
                    "partial_response": chunk,
                    "sender": "AI Assistant",
                    "streaming": True,
                    "is_sender": False,
                }))

            if ai_response_accumulator.strip():
                await self.update_message(bot_message_id, ai_response_accumulator)
                await self.send(json.dumps({
                    "id": bot_message_id,
                    "message": ai_response_accumulator,
                    "sender": "AI Assistant",
                    "streaming": False,
                    "is_sender": False,
                }))

        except Exception as e:
            print(f"Error processing message: {str(e)}")

    @sync_to_async
    def create_message(self, conversation, sender_type, message_content, sender=None, file_ids=None):
        """Create a new message with specified sender information and file attachments."""
        message = Message.active_objects.create(
            conversation=conversation,
            sender_type=sender_type,
            message=message_content,
            sender=sender
        )

        if file_ids:
            files = File.active_objects.filter(pk__in=file_ids, user=self.user)
            message.files.add(*files)

        return message

    async def load_chat_history(self, conversation):
        """Fetches chat history and sends it to the frontend."""
        chat_history = await self.fetch_chat_history_from_db(conversation)

        await self.send(text_data=json.dumps({
            "chat_history": chat_history
        }))

    @database_sync_to_async
    def fetch_chat_history_from_db(self, conversation):
        """Fetches recent chat history for AI context."""
        messages = reversed(Message.active_objects.filter(conversation=conversation).order_by('-created_at'))
        chat_history = [
            {
                "id": msg.id,
                "message": msg.message,
                "sender": msg.sender,
                "sender_type": msg.sender_type,
                "date": msg.created_at.isoformat(),
                "is_sender": msg.sender == conversation.user.email,
            }
            for msg in messages
        ]
        return (chat_history)

    @sync_to_async
    def get_conversation(self, conversation_id, user):
        """Retrieve an existing chat conversation, return None if not found."""
        return Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()

    @database_sync_to_async
    def update_message(self, message_id, new_content):
        """Update an existing AI-generated message with the final response."""
        message = Message.active_objects.filter(id=message_id).first()
        if message:
            message.message = new_content
            message.save()