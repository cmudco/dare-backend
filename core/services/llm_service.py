from django.db.models import Q
from channels.db import database_sync_to_async
from chats.constants import SenderType
from chats.models import LLM, Message, Conversation
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from typing import AsyncGenerator

class LLMService:
    """Service for handling AI message generation with optional document context."""

    def __init__(self):
        self.document_processor = DocumentProcessor()

    async def query(self, message, conversation, model_id=None, file_ids=None, user_id=None) -> AsyncGenerator[str, None]:
        """
        Handles AI message generation, dynamically selecting the appropriate model (OpenAI or Claude).
        """
        llm = await self.get_llm_model(model_id)
        chat_history = await self.get_chat_history(conversation, limit=10)

        context = ""
        if file_ids:
            context = await self.document_processor.search_similar_documents(message, file_ids, user_id)
        context = f"\nRelevant Information:\n{context}" if context else ""

        print(context)

        chat_history = "\nRecent Conversation:\n" + "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in chat_history]
        )

        full_prompt = (
            f"Context: {context}\n"
            f"{chat_history}\n"
            f"\nCurrent Question: {message}"
        )

        ai_service = OpenAIService()

        async for chunk in ai_service.stream_chat_completion(full_prompt):
            yield chunk

    @database_sync_to_async
    def get_llm_model(self, model_id=None):
        """Fetches selected LLM model or defaults to the first available."""
        return LLM.objects.filter(id=model_id).first() if model_id else LLM.objects.first()

    @database_sync_to_async
    def get_chat_history(self, conversation, limit=10):
        """Retrieves recent chat history for AI context."""
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')[:limit]
        return [
            {"role": "user" if msg.sender_type == SenderType.PLAYER else "assistant", "content": msg.message}
            for msg in reversed(messages)
        ]

    @database_sync_to_async
    def is_first_message(self, conversation):
        """Check if this is the first message exchange in the conversation."""
        return Message.active_objects.filter(conversation=conversation).count() <= 2

    @database_sync_to_async
    def update_conversation_title(self, conversation, title):
        """Update the conversation title."""
        conversation.title = title
        conversation.save()

    async def generate_title(self, user_message, ai_response):
        """Generate a short, descriptive conversation title."""
        messages = [
            {
                "role": "system",
                "content": "You are an assistant that generates short, descriptive titles (max 6 words) for conversations based on their content."
            },
            {
                "role": "user",
                "content": f"Generate a concise title for this conversation:\nUser: {user_message}\nAI: {ai_response}"
            }
        ]

        ai_service = OpenAIService(model="gpt-3.5-turbo")

        try:
            return await ai_service.get_chat_completion(user_message)
        except Exception as e:
            print(f"Error generating title: {str(e)}")
            return "New Chat"
