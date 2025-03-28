import json
from django.db.models import Q
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Message, Conversation
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from typing import AsyncGenerator

from prompts.models import Prompt

class LLMService:
    """Service for handling AI message generation with optional document context."""

    def __init__(self):
        self.document_processor = DocumentProcessor()

    async def query(self, message, conversation, llm=None, file_ids=None, user_id=None, prompt_id=None, temperature=0.7, max_tokens=2048) -> AsyncGenerator[str, None]:
        """
        Handles AI message generation with structured messages, using vector search for file context.
        """
        conversation_history = await self.get_conversation_history(conversation, limit=10)
        prompt = await self.get_prompt(prompt_id)

        messages = []

        if prompt:
            messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

        if file_ids:
            context = await self.document_processor.search_similar_documents(
                query_text=message,
                file_ids=file_ids,
                user_id=user_id,
                top_k=10
            )
            if context:
                context_parts = context.split("\n\n")
                for part in context_parts:
                    if part.strip():
                        messages.append({"role": "user", "content": part})

        messages.extend(conversation_history)

        messages.append({"role": "user", "content": f"User's message: {message}"})

        ai_service = self.get_ai_service(llm)

        async for chunk in ai_service.stream_chat_completion(messages, max_tokens=max_tokens, temperature=temperature):
            yield chunk


    @database_sync_to_async
    def get_prompt(self, prompt_id=None):
        """Fetches the prompt if the prompt_id is provided."""
        if prompt_id:
            prompt = Prompt.active_objects.filter(id=prompt_id).first()
            return prompt.content if prompt else ""
        return ""

    @database_sync_to_async
    def get_conversation_history(self, conversation, limit=10):
        """Retrieves recent chat history for AI context, ignoring placeholders."""
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')
        messages = messages[2:]
        return [
            {"role": "user" if msg.sender_type == SenderType.PLAYER else "assistant", "content": msg.message}
            for msg in reversed(messages)
        ]

    def get_ai_service(self, llm: LLM):
        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm)
        else:
            return ClaudeService(llm=llm)