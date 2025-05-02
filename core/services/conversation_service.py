from channels.db import database_sync_to_async
from conversations.models import LLM, Message, Conversation
from core.services.openai_service import OpenAIService
from conversations.constants import SenderType
from asgiref.sync import sync_to_async
import logging
from conversations.api.serializers import MessageSerializer
from djangorestframework_camel_case.util import camelize

logger = logging.getLogger(__name__)

class ConversationService:
    """Handles conversation metadata like title generation and message management."""

    async def fetch_chat_history_from_db(self, conversation):
        """Fetches recent chat history for AI context, including snippets."""
        messages = await database_sync_to_async(
            lambda: list(
                Message.active_objects.filter(conversation=conversation)
                .select_related('llm')  # Preload llm relationship
                .prefetch_related('snippets')
                .order_by('-created_at')
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

    async def get_latest_user_message(self, conversation):
        """Retrieve the latest user message for the given conversation."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation,
                sender_type=SenderType.PLAYER
            ).order_by('-created_at').first()
        )()

    async def get_latest_ai_message(self, conversation):
        """Retrieve the latest AI message for the given conversation."""
        return await database_sync_to_async(
            lambda: Message.active_objects.filter(
                conversation=conversation,
                sender_type=SenderType.AI_ASSISTANT
            ).order_by('-created_at').first()
        )()

    async def edit_message(self, message_id, new_content, conversation):
        """Edit the latest user message with new content."""
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

    async def regenerate_message(self, message_id, new_content, conversation):
        """Regenerate the latest AI message with new content."""
        message = await database_sync_to_async(
            lambda: Message.active_objects.get(id=message_id)
        )()
        latest_ai_message = await self.get_latest_ai_message(conversation)
        if not latest_ai_message or str(latest_ai_message.id) != message_id:
            raise ValueError("Can only regenerate the latest AI message")

        if not message.is_regenerated:
            message.original_message = message.message
            message.is_regenerated = True
        message.message = new_content
        await database_sync_to_async(message.save)()
        return message