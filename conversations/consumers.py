import json
from channels.generic.websocket import AsyncWebsocketConsumer
from urllib.parse import parse_qs
from asgiref.sync import sync_to_async
from django.conf import settings
from channels.exceptions import DenyConnection
from pydantic import ValidationError

from conversations.models import Conversation, Message, LLM
from core.services.conversation_service import ConversationService
from core.services.llm_service import LLMService
from .constants import SenderType
from django.contrib.auth import get_user_model
from files.models import File
from channels.db import database_sync_to_async
import asyncio
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize

User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 1024
    DEFAULT_MAX_CONTEXT_SNIPPETS = 4
    DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD = 0.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conversation_service = ConversationService()
        self.llm_service = LLMService()
        self.user = None
        self.conversation = None
        self.conversation_id = None

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
            await self.close()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        pass

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming WebSocket messages, process them, and stream AI responses.
        """
        try:
            data = json.loads(text_data)
            action = data.get("action")

            if action != "edit_message":
                llm_id = data.get("llm_id")
                llm = await database_sync_to_async(LLM.objects.filter(id=llm_id).first)() if llm_id else await database_sync_to_async(LLM.objects.first)()

                has_sufficient_credits, error_details = await self.conversation_service.check_user_has_sufficient_credits(
                    self.user, llm
                )

                if not has_sufficient_credits:
                    await self.send(json.dumps(error_details))
                    return

            if action == "edit_message":
                await self.handle_edit_message(data)
            elif action == "regenerate_response":
                await self.handle_regenerate_response(data)
            else:
                await self.handle_new_message(data)
        except Exception as e:
            await self.send(json.dumps({
                "error": "processing_error",
                "message": "An error occurred while processing your message."
            }))

    async def handle_new_message(self, data):
        msg_content = data.get("message", "").strip()
        sender_type = data.get("sender_type", SenderType.PLAYER)
        file_ids = data.get("file_ids", [])
        tag_ids = data.get("tag_ids", [])
        llm_id = data.get("llm_id")
        prompt_id = data.get("prompt_id", None)
        temperature = data.get("temperature", self.DEFAULT_TEMPERATURE)
        max_tokens = data.get("max_tokens", self.DEFAULT_MAX_TOKENS)
        max_context_snippets = data.get("max_context_snippets", self.DEFAULT_MAX_CONTEXT_SNIPPETS)
        document_similarity_threshold = data.get("document_similarity_threshold", self.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD)

        message_obj = await self.conversation_service.create_message(
            self.conversation, sender_type, msg_content, self.user.email, file_ids
        )
        await self.send(await self.format_message(message_obj, is_sender=True))

        asyncio.create_task(self.handle_title_generation(msg_content))

        llm = await database_sync_to_async(LLM.objects.filter(id=llm_id).first)() if llm_id else await database_sync_to_async(LLM.objects.first)()

        has_sufficient_credits, error_details = await self.conversation_service.check_user_has_sufficient_credits(
            self.user, llm
        )

        if not has_sufficient_credits:
            await self.send(json.dumps(error_details))
            return

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

    async def handle_edit_message(self, data):
        message_id = data.get("message_id")
        new_content = data.get("message", "").strip()

        if not message_id or not new_content:
            await self.send(json.dumps({"error": "Missing message_id or message content"}))
            return

        latest_user_message = await self.conversation_service.get_latest_user_message(self.conversation)
        if not latest_user_message or latest_user_message.id != message_id:
            await self.send(json.dumps({"error": "Can only edit the latest user message"}))
            return

        updated_message = await self.conversation_service.edit_message(message_id, new_content, self.conversation)
        await self.send(await self.format_message(updated_message, is_sender=True))

    async def handle_regenerate_response(self, data):
        message_id = data.get("message_id")
        llm_id = data.get("llm_id")
        temperature = data.get("temperature", self.DEFAULT_TEMPERATURE)
        max_tokens = data.get("max_tokens", self.DEFAULT_MAX_TOKENS)
        max_context_snippets = data.get("max_context_snippets", self.DEFAULT_MAX_CONTEXT_SNIPPETS)
        document_similarity_threshold = data.get("document_similarity_threshold", self.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD)
        prompt_id = data.get("prompt_id")
        file_ids = data.get("file_ids", [])
        tag_ids = data.get("tag_ids", [])

        if not message_id:
            await self.send(json.dumps({"error": "Missing message_id"}))
            return

        ai_message = await database_sync_to_async(
            lambda: Message.active_objects.select_related('llm').filter(
                id=message_id,
                sender_type=SenderType.AI_ASSISTANT
            ).first()
        )()
        if not ai_message:
            await self.send(json.dumps({"error": "AI message not found"}))
            return

        preceding_user_message = await self._get_preceding_user_message(ai_message)
        if not preceding_user_message:
            await self.send(json.dumps({"error": "No preceding user message found to regenerate response"}))
            return

        llm = await database_sync_to_async(LLM.objects.filter(id=llm_id).first)() if llm_id else await database_sync_to_async(lambda: ai_message.llm)()
        if not llm:
            llm = await database_sync_to_async(lambda: LLM.objects.first())()

        has_sufficient_credits, error_details = await self.conversation_service.check_user_has_sufficient_credits(
            self.user, llm
        )

        if not has_sufficient_credits:
            await self.send(json.dumps(error_details))
            return

        bot_message_id = str(ai_message.id)
        ai_response_accumulator = ""
        token_usage = None

        await self.send(json.dumps(camelize({
            "type": "ai_stream",
            "id": bot_message_id,
            "message": ai_response_accumulator,
            "senderName": "AI Assistant",
            "senderType": SenderType.AI_ASSISTANT,
            "isSender": False,
            "streaming": True,
            "regenerate": True,
            "date": ai_message.created_at.isoformat(),
        })))

        async for chunk, usage in self.llm_service.query(
            preceding_user_message.message,
            self.conversation,
            llm,
            file_ids,
            tag_ids=tag_ids,
            user_id=self.user.id,
            prompt_id=prompt_id,
            temperature=temperature,
            max_tokens=max_tokens,
            max_context_snippets=max_context_snippets,
            document_similarity_threshold=document_similarity_threshold,
            message_obj=ai_message
        ):
            if usage:
                token_usage = usage
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
                    "regenerate": True,
                    "date": ai_message.created_at.isoformat(),
                }
                await self.send(json.dumps(camelize(payload)))

        if ai_response_accumulator.strip():
            try:
                await database_sync_to_async(self.conversation_service.finalize_ai_message_with_billing)(ai_message, ai_response_accumulator, token_usage)
                ai_message.is_regenerated = True
                if not ai_message.original_message:
                    ai_message.original_message = ai_message.message
                await database_sync_to_async(ai_message.save)(update_fields=['is_regenerated', 'original_message'])
                await self.send(await self.format_message(
                    ai_message, message=ai_response_accumulator, streaming=False, regenerate=True
                ))
            except ValidationError:
                await self.send(json.dumps({"error": "Insufficient wallet balance"}))

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
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS,
        max_context_snippets=DEFAULT_MAX_CONTEXT_SNIPPETS,
        document_similarity_threshold=DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD
    ):
        """Handles AI response streaming and updates the message."""
        bot_message_id = str(bot_message_obj.id)
        ai_response_accumulator = ""
        token_usage = None

        async for chunk, usage in self.llm_service.query(
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
            if usage:
                token_usage = usage
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
                    "regenerate": False,
                    "date": bot_message_obj.created_at.isoformat(),
                }
                await self.send(json.dumps(camelize(payload)))

        if ai_response_accumulator.strip():
            try:
                await database_sync_to_async(self.conversation_service.finalize_ai_message_with_billing)(bot_message_obj, ai_response_accumulator, token_usage)
                bot_message_obj = await database_sync_to_async(
                    lambda: Message.active_objects.prefetch_related('snippets').get(id=bot_message_obj.id)
                )()
                await self.send(await self.format_message(
                    bot_message_obj, message=ai_response_accumulator, streaming=False, regenerate=False
                ))
            except ValidationError:
                await self.send(json.dumps({"error": "Insufficient wallet balance"}))

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

    async def format_message(self, message_obj, message=None, is_sender=False, streaming=False, regenerate=False):
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
            "regenerate": regenerate,
            "date": message_obj.created_at.isoformat(),
            "llmId": llm_id,
            "snippets": serialized_data.get("snippets", []),
            "isEdited": message_obj.is_edited,
            "isRegenerated": message_obj.is_regenerated,
            "originalMessage": message_obj.original_message,
        }
        return json.dumps(camelize(response))

    async def _get_preceding_user_message(self, ai_message):
        """Retrieve the preceding user message for a given AI message."""
        preceding_messages = await database_sync_to_async(
            lambda: list(Message.active_objects.filter(conversation=self.conversation, created_at__lt=ai_message.created_at).order_by('-created_at'))
        )()
        return next((msg for msg in preceding_messages if msg.sender_type == SenderType.PLAYER), None)

    async def _get_file_ids(self, message):
        """Retrieve file IDs associated with a message."""
        return await database_sync_to_async(lambda: list(message.files.values_list('id', flat=True)))()