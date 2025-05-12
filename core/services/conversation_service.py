from decimal import Decimal
from typing import Dict, Optional
from django.db import models
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from conversations.models import LLM, Message, Conversation
from core.services.openai_service import OpenAIService
from conversations.constants import SenderType
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize

from users.models import User

class ConversationService:
    """Handles conversation metadata and message management."""

    async def fetch_chat_history_from_db(self, conversation: Conversation, limit: int = 50):
        """Fetches recent chat history for AI context."""
        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .select_related('llm')
                .prefetch_related('snippets')
                .order_by('-created_at')[:limit]
            )
        )()

        serialized_messages = await database_sync_to_async(
            lambda: MessageSerializer(reversed(messages), many=True).data
        )()

        user_email = await self.get_user_email(conversation)

        history = [
            {
                "id": msg["id"],
                "message": msg["message"],
                "sender": msg["sender_name"],
                "sender_type": msg["sender_type"],
                "date": msg["created_at"],
                "isSender": msg["sender_name"] == user_email,
                "llmId": msg["llm"],
                "snippets": msg.get("snippets", []),
                "is_liked": msg.get("is_liked", False),
                "is_disliked": msg.get("is_disliked", False),
                "isEdited": msg.get("is_edited", False),
                "isRegenerated": msg.get("is_regenerated", False),
                "originalMessage": msg.get("original_message", None),
            }
            for msg in serialized_messages
        ]
        return camelize(history)

    async def get_user_email(self, conversation: Conversation) -> str:
        """Fetch user email associated with the conversation."""
        return await database_sync_to_async(lambda: getattr(conversation.user, 'email', ''))()

    async def create_message(
        self, conversation: Conversation, sender_type: str, message_content: str,
        sender: str = None, file_ids: list = None, llm: LLM = None
    ) -> Message:
        """Create a new message with file attachments."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                message=message_content,
                sender=sender,
                llm=llm
            )
        )()

        if file_ids and sender:
            files = await database_sync_to_async(
                lambda: list(conversation.user.files.filter(pk__in=file_ids))
            )()
            if files:
                await database_sync_to_async(lambda: message.files.add(*files))()

        return message

    async def get_conversation(self, conversation_id: str, user: 'User') -> Optional[Conversation]:
        """Retrieve a conversation by ID for the given user."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()
        )()

    async def is_first_message(self, conversation: Conversation) -> bool:
        """Check if this is the first message in the conversation."""
        count = await database_sync_to_async(
            lambda: Message.active_objects.filter(conversation=conversation).count()
        )()
        return count <= 2

    async def update_conversation_title(self, conversation: Conversation, title: str):
        """Update the conversation title."""
        await database_sync_to_async(
            lambda: Conversation.active_objects.filter(id=conversation.id).update(title=title)
        )()

    async def generate_title(self, user_message: str, ai_response: str = "") -> str:
        """Generate a concise conversation title."""
        messages = [
            {
                "role": "system",
                "content": "Generate a short, descriptive conversation title (max 6 words)."
            },
            {
                "role": "user",
                "content": f"Title for: User: {user_message}\nAI: {ai_response}"
            }
        ]

        llm = await self.get_gpt_35_turbo_model()
        ai_service = OpenAIService(llm=llm)
        try:
            return await ai_service.get_chat_completion(messages)
        except Exception as e:
            return "New Chat"

    async def get_gpt_35_turbo_model(self) -> LLM:
        """Fetch the gpt-3.5-turbo LLM."""
        llm = await database_sync_to_async(
            lambda: LLM.objects.filter(identifier="gpt-3.5-turbo", provider="openai").first()
        )()
        return llm or await database_sync_to_async(lambda: LLM.objects.filter(provider="openai").first())()

    async def get_latest_user_message(self, conversation: Conversation) -> Optional[Message]:
        """Retrieve the latest user message."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation, sender_type=SenderType.PLAYER
            ).order_by('-created_at').first()
        )()

    async def edit_message(self, message_id: str, new_content: str, conversation: Conversation) -> Message:
        """Edit the latest user message."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.get(id=message_id)
        )()
        latest_user_message = await self.get_latest_user_message(conversation)
        if not latest_user_message or str(latest_user_message.id) != message_id:
            raise ValueError("Can only edit the latest user message")

        if not message.is_edited:
            message.original_message = message.message
            message.is_edited = True
        message.message = new_content
        await database_sync_to_async(message.save)()
        return message

    def finalize_ai_message_with_billing(self, message_obj: Message, ai_response: str, token_usage: Dict) -> Message:
        """Finalize AI message with billing (delegated to BillingService)."""
        from core.services.billing_service import BillingService
        billing_service = BillingService()
        return billing_service.finalize_ai_message(message_obj, ai_response, token_usage)