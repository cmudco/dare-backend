from django.db.models import Q
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Message, Conversation
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

        message = (
            f"Context: {context}"
            f"Current Question: {message}"
        )

        messages = chat_history + [{"role": "user", "content": message}]

        print(messages)

        ai_service = ai_service = self.get_ai_service(llm)

        async for chunk in ai_service.stream_chat_completion(messages):
            yield chunk

    @database_sync_to_async
    def get_llm_model(self, model_id=None):
        """Fetches selected LLM model or defaults to the first available."""
        return LLM.objects.filter(id=model_id).first() if model_id else LLM.objects.first()

    @database_sync_to_async
    def get_chat_history(self, conversation, limit=10):
        """Retrieves recent chat history for AI context, ignoring placeholders."""
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')

        messages = messages[2:] if len(messages) > 2 else messages

        return [
            {"role": "user" if msg.sender_type == SenderType.PLAYER else "assistant", "content": msg.message}
            for msg in reversed(messages)
        ]

    
    def get_ai_service(self, llm: LLM):
        """Returns the appropriate AI service based on the LLM provider and model identifier."""
        print(llm.provider)
        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(model=llm.identifier)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(model=llm.identifier)
        else:
            return ClaudeService(model=llm.identifier)
