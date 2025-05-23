from abc import ABC, abstractmethod
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from typing import AsyncGenerator, Dict, Tuple
from files.models import File
from prompts.models import Prompt
from core.services.vector_service import get_vector_service_async

class AIService(ABC):
    """Abstract base class for AI services."""
    @abstractmethod
    async def stream_chat_completion(self, messages: list, max_tokens: int, temperature: float) -> AsyncGenerator[Tuple[str, Dict], None]:
        pass

class LLMService:
    """Service for handling AI message generation with document context."""

    def __init__(self):
        self.document_processor = DocumentProcessor(vector_service=None)

    async def query(
        self,
        message: str,
        conversation: 'Conversation',
        llm: LLM = None,
        file_ids: list = None,
        tag_ids: list = None,
        user_id: int = None,
        prompt_id: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_context_snippets: int = 4,
        document_similarity_threshold: float = 0.5,
        history_limit: int = 10,
        message_obj: Message = None,
        full_file_content: str = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Generate AI response with context."""
        try:
            if not message.strip():
                raise ValueError("User message cannot be empty")

            conversation_history = await self.get_conversation_history(conversation, limit=history_limit) if conversation else []
            prompt = await self.get_prompt(prompt_id)
            messages = []

            if prompt and prompt.strip():
                messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

            if full_file_content:
                messages.append({"role": "user", "content": f"File content: {full_file_content}"})
            elif file_ids:
                all_file_ids = set(file_ids or [])
                if tag_ids:
                    tagged_file_ids = await self.get_files_from_tags(tag_ids, user_id)
                    all_file_ids.update(tagged_file_ids)

                if user_id and user_id != self.document_processor.user_id:
                    self.document_processor.user_id = user_id
                    self.document_processor.vector_service = await get_vector_service_async(user_id)

                context = await self.document_processor.search_similar_documents(
                    query_text=message,
                    file_ids=list(all_file_ids),
                    user_id=user_id,
                    top_k=max_context_snippets,
                    similarity_threshold=document_similarity_threshold,
                    message_obj=message_obj
                )
                if context:
                    for part in context.split("\n\n"):
                        if part.strip():
                            messages.append({"role": "user", "content": part})

            messages.extend([msg for msg in conversation_history if msg["content"].strip()])
            messages.append({"role": "user", "content": f"User's message: {message}"})

            ai_service = self._get_ai_service(llm)
            async for chunk, usage in ai_service.stream_chat_completion(messages, max_tokens, temperature):
                yield chunk, usage

        except Exception as e:
            yield f"Error: {str(e)}", None

    @database_sync_to_async
    def get_prompt(self, prompt_id: str = None) -> str:
        """Fetches the prompt if the prompt_id is provided."""
        if prompt_id:
            prompt = Prompt.active_objects.filter(id=prompt_id).first()
            return prompt.content if prompt else ""
        return ""

    @database_sync_to_async
    def get_conversation_history(self, conversation: 'Conversation', limit: int = 10) -> list:
        """Retrieves recent chat history for AI context, ignoring placeholders."""
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')
        messages = messages[2:limit+2] if limit > 0 else messages[2:]
        return [
            {"role": "user" if msg.sender_type == SenderType.PLAYER else "assistant", "content": msg.message}
            for msg in reversed(messages)
        ]

    @database_sync_to_async
    def get_files_from_tags(self, tag_ids: list, user_id: int) -> list:
        """Fetch file IDs from tags."""
        if not tag_ids:
            return []
        return list(File.active_objects.filter(tags__id__in=tag_ids, user_id=user_id).distinct().values_list('id', flat=True))

    def _get_ai_service(self, llm: LLM) -> AIService:
        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm)
        return ClaudeService(llm=llm)