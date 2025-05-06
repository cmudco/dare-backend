from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Message
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from typing import AsyncGenerator, Dict, Tuple
from files.models import File
from prompts.models import Prompt
from core.services.vector_service import get_vector_service, get_vector_service_async

class LLMService:
    """Service for handling AI message generation with optional document context."""

    def __init__(self):
        self.document_processor = DocumentProcessor(vector_service=None)

    async def query(
        self,
        message,
        conversation,
        llm=None,
        file_ids=None,
        tag_ids=None,
        user_id=None,
        prompt_id=None,
        temperature=0.7,
        max_tokens=2048,
        max_context_snippets=4,
        document_similarity_threshold=0.5,
        message_obj=None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Handles AI message generation with structured messages, using vector search for file context.
        """
        conversation_history = await self.get_conversation_history(conversation, limit=10)
        prompt = await self.get_prompt(prompt_id)

        messages = []
        if prompt and prompt.strip():
            messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

        all_file_ids = set(file_ids or [])

        if tag_ids:
            tagged_file_ids = await self.get_files_from_tags(tag_ids, user_id)
            all_file_ids.update(tagged_file_ids)

        if user_id and user_id != self.document_processor.user_id:
            self.document_processor.user_id = user_id
            self.document_processor.vector_service = await get_vector_service_async(user_id)

        if all_file_ids:
            context = await self.document_processor.search_similar_documents(
                query_text=message,
                file_ids=list(all_file_ids),
                user_id=user_id,
                top_k=max_context_snippets,
                similarity_threshold=document_similarity_threshold,
                message_obj=message_obj
            )
            if context:
                context_parts = context.split("\n\n")
                for part in context_parts:
                    if part.strip():
                        messages.append({"role": "user", "content": part})

        filtered_history = [msg for msg in conversation_history if msg["content"].strip()]

        messages.extend(filtered_history)
        if not message.strip():
            raise ValueError("User message cannot be empty.")
        messages.append({"role": "user", "content": f"User's message: {message}"})

        ai_service = self.get_ai_service(llm)

        async for chunk, usage in ai_service.stream_chat_completion(messages, max_tokens=max_tokens, temperature=temperature):
            yield chunk, usage

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

    @database_sync_to_async
    def get_files_from_tags(self, tag_ids, user_id):
        if not tag_ids:
            return []
        tagged_files = File.active_objects.filter(
            tags__id__in=tag_ids,
            user_id=user_id
        ).distinct().values_list('id', flat=True)
        return list(tagged_files)

    def get_ai_service(self, llm: LLM):
        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm)
        else:
            return ClaudeService(llm=llm)