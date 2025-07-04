from abc import ABC, abstractmethod
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from typing import AsyncGenerator, Dict, Tuple
from files.models import File, Folder
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
        from core.services.file_processor import FileProcessor
        self.file_processor = FileProcessor()

    async def query(
        self,
        message: str,
        conversation: 'Conversation',
        llm: LLM = None,
        file_ids: list = None,
        embedding_ids: list = None,
        tag_ids: list = None,
        folder_ids: list = None,
        user_id: int = None,
        prompt_id: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_context_snippets: int = 4,
        document_similarity_threshold: float = 0.5,
        history_limit: int = 20,
        referenced_conversation_ids: list = None,
        referenced_conversation_history_limit: int = 10,
        message_obj: Message = None
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

            if referenced_conversation_ids:
                referenced_context = await self.get_referenced_conversations_context(
                    referenced_conversation_ids,
                    user_id,
                    referenced_conversation_history_limit
                )
                if referenced_context:
                    messages.append({"role": "user", "content": referenced_context})

            if file_ids:
                file_contents = await self.get_full_file_contents(file_ids, user_id)
                if file_contents:
                    for file_content in file_contents:
                        messages.append({"role": "user", "content": file_content})

            if embedding_ids or tag_ids or folder_ids:
                all_embedding_file_ids = set(embedding_ids or [])
                if tag_ids:
                    tagged_file_ids = await self.get_files_from_tags(tag_ids, user_id)
                    all_embedding_file_ids.update(tagged_file_ids)
                if folder_ids:
                    folder_file_ids = await self.get_files_from_folders(folder_ids, user_id)
                    all_embedding_file_ids.update(folder_file_ids)

                if all_embedding_file_ids:
                    if user_id and user_id != self.document_processor.user_id:
                        self.document_processor.user_id = user_id
                        self.document_processor.vector_service = await get_vector_service_async(user_id)

                    context = await self.document_processor.search_similar_documents(
                        query_text=message,
                        file_ids=list(all_embedding_file_ids),
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
    def get_conversation_history(self, conversation: 'Conversation', limit: int = 10, ) -> list:
        """Retrieves recent chat history for AI context, ignoring placeholders."""
        messages = Message.active_objects.filter(conversation=conversation).order_by('-created_at')
        if limit >= 50:
            messages = messages[2:]
        else:
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

    @database_sync_to_async
    def get_files_from_folders(self, folder_ids: list, user_id: int) -> list:
        """Fetch file IDs from folders."""
        if not folder_ids:
            return []
        return list(File.active_objects.filter(folders__id__in=folder_ids, user_id=user_id).distinct().values_list('id', flat=True))

    @database_sync_to_async
    def get_full_file_contents(self, file_ids: list, user_id: int) -> list:
        """Read full content from files for the given file IDs."""
        if not file_ids:
            return []

        file_contents = []
        files = File.active_objects.filter(id__in=file_ids, user_id=user_id)

        for file in files:
            try:
                content = self.file_processor.read_file_content(file)
                file_name = file.name or file.file.name
                formatted_content = f"File: {file_name}\n\n{content}"
                file_contents.append(formatted_content)
            except Exception as e:
                continue

        return file_contents

    @database_sync_to_async
    def get_referenced_conversations_context(self, conversation_ids: list, user_id: int, history_limit: int = 10) -> str:
        """Fetch context from referenced conversations."""
        if not conversation_ids:
            return ""

        context_parts = []
        conversations = Conversation.active_objects.filter(
            conversation_id__in=conversation_ids,
            user_id=user_id
        )

        for conversation in conversations:
            messages = Message.active_objects.filter(
                conversation=conversation
            ).order_by('-created_at')[:history_limit]

            if messages:
                conversation_title = conversation.title or "Untitled Conversation"
                context_parts.append(f"=== Referenced Conversation: {conversation_title} ===")

                for msg in reversed(messages):
                    role = "User" if msg.sender_type == SenderType.PLAYER else "Assistant"
                    context_parts.append(f"{role}: {msg.message}")

                context_parts.append("=== End of Referenced Conversation ===\n")

        if context_parts:
            full_context = "\n".join(context_parts)
            return f"Referenced conversation context for additional background:\n\n{full_context}"

        return ""

    def _get_ai_service(self, llm: LLM) -> AIService:
        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm)
        return ClaudeService(llm=llm)