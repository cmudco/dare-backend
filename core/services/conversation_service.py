from channels.db import database_sync_to_async
from conversations.models import LLM, Message, Conversation
from core.services.openai_service import OpenAIService
from conversations.constants import SenderType
from asgiref.sync import sync_to_async
import logging
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize  # Import camelize

logger = logging.getLogger(__name__)

class ConversationService:
    """Handles conversation metadata like title generation."""

    async def fetch_chat_history_from_db(self, conversation):
        """Fetches recent chat history for AI context, including snippets."""
        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .order_by('-created_at')
                .prefetch_related('snippets')
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
                "llmId": msg["llm"] if "llm" in msg else None,
                "snippets": msg.get("snippets", [])
            }
            for msg in serialized_messages
        ]

        # Apply camelCase transformation
        return camelize(history)

    async def get_user_email(self, conversation):
        """Safely fetch the email of the user associated with the conversation."""
        return await database_sync_to_async(
            lambda: getattr(conversation.user, 'email', '')
        )()

    async def update_message(self, message_id, new_content):
        """Update an existing AI-generated message with the final response."""
        await database_sync_to_async(
            lambda: Message.active_objects.filter(id=message_id).update(message=new_content)
        )()

    async def create_message(self, conversation, sender_type, message_content, sender=None, file_ids=None, user=None, llm=None):
        """Create a new message with specified sender information and file attachments."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.create(
                conversation=conversation,
                sender_type=sender_type,
                message=message_content,
                sender=sender,
                llm=llm
            )
        )()

        if file_ids and user:
            files = await database_sync_to_async(
                lambda: list(user.files.filter(pk__in=file_ids))
            )()
            if files:
                await database_sync_to_async(
                    lambda: message.files.add(*files)
                )()

        return message

    async def get_conversation(self, conversation_id, user):
        """Retrieve an existing chat conversation, return None if not found."""
        return await database_sync_to_async(
            lambda: Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()
        )()

    async def is_first_message(self, conversation):
        """Check if this is the first message exchange in the conversation."""
        count = await database_sync_to_async(
            lambda: Message.active_objects.filter(conversation=conversation).count()
        )()
        return count <= 2

    async def update_conversation_title(self, conversation, title):
        """Update the conversation title."""
        await database_sync_to_async(
            lambda: Conversation.active_objects.filter(id=conversation.id).update(title=title)
        )()

    async def get_gpt_35_turbo_model(self):
        """Fetch the LLM object for gpt-3.5-turbo from the database."""
        llm = await database_sync_to_async(
            lambda: LLM.objects.filter(identifier="gpt-3.5-turbo", provider="openai").first()
        )()
        if not llm:
            logger.warning("gpt-3.5-turbo not found in LLM table, falling back to first OpenAI model")
            llm = await database_sync_to_async(
                lambda: LLM.objects.filter(provider="openai").first()
            )()
        return llm

    async def generate_title(self, user_message, ai_response=""):
        """Generate a short, descriptive conversation title (max 6 words)."""
        messages = [
            {
                "role": "system",
                "content": "You are an assistant that generates short, descriptive conversation titles. "
                           "Keep the title concise, meaningful, and strictly 6 words or fewer."
            },
            {
                "role": "user",
                "content": f"Generate a short title (max 6 words) based on this conversation:\n"
                           f"User: {user_message}\nAI: {ai_response}\n\n"
                           f"Response must be at most 6 words long."
            }
        ]

        llm = await self.get_gpt_35_turbo_model()
        ai_service = OpenAIService(llm=llm)

        try:
            return await ai_service.get_chat_completion(messages)
        except Exception as e:
            logger.exception(f"Error generating title: {str(e)}")
            return "New Chat"
