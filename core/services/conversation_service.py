from channels.db import database_sync_to_async
from conversations.models import Message, Conversation
from core.services.openai_service import OpenAIService
from conversations.constants import SenderType
from asgiref.sync import sync_to_async

class ConversationService:
    """Handles conversation metadata like title generation."""

    @database_sync_to_async
    def fetch_chat_history_from_db(self, conversation):
        """Fetches recent chat history for AI context."""
        messages = reversed(Message.active_objects.filter(conversation=conversation).order_by('-created_at'))
        return [
            {
                "id": msg.id,
                "message": msg.message,
                "sender": msg.sender,
                "sender_type": msg.sender_type,
                "date": msg.created_at.isoformat(),
                "isSender": msg.sender == conversation.user.email,
            }
            for msg in messages
        ]

    @database_sync_to_async
    def update_message(self, message_id, new_content):
        """Update an existing AI-generated message with the final response."""
        message = Message.active_objects.filter(id=message_id).first()
        if message:
            message.message = new_content
            message.save()

    @sync_to_async
    def create_message(self, conversation, sender_type, message_content, sender=None, file_ids=None, user=None):
        """Create a new message with specified sender information and file attachments."""
        message = Message.active_objects.create(
            conversation=conversation,
            sender_type=sender_type,
            message=message_content,
            sender=sender
        )

        if file_ids:
            files = user and user.files.filter(pk__in=file_ids)
            if files:
                message.files.add(*files)

        return message

    @sync_to_async
    def get_conversation(self, conversation_id, user):
        """Retrieve an existing chat conversation, return None if not found."""
        return Conversation.active_objects.filter(conversation_id=conversation_id, user=user).first()

    @database_sync_to_async
    def is_first_message(self, conversation):
        """Check if this is the first message exchange in the conversation."""
        return Message.active_objects.filter(conversation=conversation).count() <= 2

    @database_sync_to_async
    def update_conversation_title(self, conversation, title):
        """Update the conversation title."""
        conversation.title = title
        conversation.save()

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
        ai_service = OpenAIService(model="gpt-3.5-turbo")

        try:
            return await ai_service.get_chat_completion(messages)
        except Exception as e:
            prlogger.exceptionint(f"Error generating title: {str(e)}")
            return "New Chat"
