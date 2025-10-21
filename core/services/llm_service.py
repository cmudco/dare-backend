from abc import ABC, abstractmethod
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.file_processor import FileProcessor
from core.services.whisper_service import WhisperService
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from typing import AsyncGenerator, Dict, Tuple, Optional, Any, List
from files.models import File, Folder
from prompts.models import Prompt
from core.services.vector_service import get_vector_service_async
import logging
import base64

logger = logging.getLogger(__name__)

class AIService(ABC):
    """Abstract base class for AI services."""
    @abstractmethod
    async def stream_chat_completion(self, messages: list, max_tokens: int, temperature: float, images: list = None, tools: list = None) -> AsyncGenerator[Tuple[str, Dict], None]:
        pass
    @abstractmethod
    async def get_chat_completion(self, messages: list, max_tokens: int, temperature: float, structured_spec: Optional[Dict[str, Any]] = None) -> str:
        """Non-streaming chat completion, optionally honoring structured outputs spec."""
        pass

class LLMService:
    """Service for handling AI message generation with document context."""

    def __init__(self):
        self.document_processor = DocumentProcessor(vector_service=None)
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
        user = None,
        prompt_id: str = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        max_context_snippets: int = 4,
        document_similarity_threshold: float = 0.5,
        history_limit: int = 20,
        referenced_conversation_ids: list = None,
        message_obj: Message = None,
        workflow_run_step_obj=None,
        images: list = None,  # Vision support: list of dicts with 'preview' (base64), 'name', 'type'
        media_ids: list = None,  # NEW: Media file IDs (images/videos) - persistent files from upload
        # New optional params for SocraticBooks-style prompt construction
        socratic_mode: bool = False,
        bot_meta: Dict = None,
        advanced_mode: bool = False,
        web_search_enabled: bool = False,
        structured_spec: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Generate AI response with context.

        Args:
            user: User object for API key resolution and file access

        Note: Referenced conversations always include full history (no limit).
        """
        try:
            llm = llm or LLM.objects.filter(is_active=True).first()
            if not llm:
                yield "Error: No active LLM found", None
                return

            # If Socratic mode is enabled, construct prompts using SocraticBooks logic
            if socratic_mode:
                messages = await (
                    self._build_advanced_messages if advanced_mode else self._build_socratic_messages
                )(
                    message=message,
                    conversation=conversation,
                    user_id=user.id if user else None,
                    file_ids=[],
                    embedding_ids=file_ids or [],
                    tag_ids=tag_ids or [] if advanced_mode else [],
                    folder_ids=folder_ids or [] if advanced_mode else [],
                    history_limit=history_limit,
                    max_context_snippets=max_context_snippets,
                    document_similarity_threshold=document_similarity_threshold,
                    message_obj=message_obj,
                    workflow_run_step_obj=workflow_run_step_obj,
                    bot_meta=bot_meta or {},
                )

                # Process media files and combine with temporary images for Socratic mode
                all_images = images or []
                if media_ids:
                    user_id = user.id if user else None
                    if user_id:
                        media_images = await self.get_media_files_as_images(media_ids, user_id)
                        all_images = all_images + media_images

                # Add video transcriptions to message context (for audio content)
                if all_images:
                    messages = await self.add_video_transcriptions_to_context(all_images, messages, user)

                ai_service = await self._get_ai_service(llm, user)
                tools = self._get_web_search_tools(llm) if web_search_enabled else None
                if structured_spec:
                    text = await ai_service.get_chat_completion(messages, max_tokens, temperature, structured_spec=structured_spec)
                    yield text, None
                else:
                    async for chunk, usage in ai_service.stream_chat_completion(messages, max_tokens, temperature, images=all_images, tools=tools):
                        yield chunk, usage
                return

            conversation_history = await self.get_conversation_history(conversation, limit=history_limit) if conversation else []
            prompt = await self.get_prompt(prompt_id)
            messages = []

            if prompt and prompt.strip():
                messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

            if referenced_conversation_ids:
                referenced_context = await self.get_referenced_conversations_context(
                    referenced_conversation_ids,
                    user.id if user else None,
                    None
                )
                if referenced_context:
                    messages.append({"role": "user", "content": referenced_context})

            if file_ids:
                file_contents = await self.get_full_file_contents(file_ids,)
                if file_contents:
                    for file_content in file_contents:
                        messages.append({"role": "user", "content": file_content})

            if embedding_ids or tag_ids or folder_ids:
                all_embedding_file_ids = set(embedding_ids or [])
                user_id = user.id if user else None
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

                    effective_threshold = 0.05 if socratic_mode else document_similarity_threshold

                    context = await self.document_processor.search_similar_documents(
                        query_text=message,
                        file_ids=list(all_embedding_file_ids),
                        user_id=user_id,
                        top_k=max_context_snippets,
                        similarity_threshold=effective_threshold,
                        message_obj=message_obj,
                        workflow_run_step_obj=workflow_run_step_obj
                    )
                    if context:
                        for part in context.split("\n\n"):
                            if part.strip():
                                messages.append({"role": "user", "content": part})

            messages.extend([msg for msg in conversation_history if msg["content"].strip()])
            messages.append({"role": "user", "content": f"User's message: {message}"})

            # Process media files and combine with temporary images
            all_images = images or []
            if media_ids:
                user_id = user.id if user else None
                if user_id:
                    media_images = await self.get_media_files_as_images(media_ids, user_id)
                    all_images = all_images + media_images

            # Add video transcriptions to message context (for audio content)
            if all_images:
                messages = await self.add_video_transcriptions_to_context(all_images, messages, user)

            ai_service = await self._get_ai_service(llm, user)
            tools = self._get_web_search_tools(llm) if web_search_enabled else None
            if structured_spec:
                text = await ai_service.get_chat_completion(messages, max_tokens, temperature, structured_spec=structured_spec)
                yield text, None
            else:
                async for chunk, usage in ai_service.stream_chat_completion(messages, max_tokens, temperature, images=all_images, tools=tools):
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
    def get_full_file_contents(self, file_ids: list,) -> list:
        """Read full content from files for the given file IDs."""
        if not file_ids:
            return []

        file_contents = []
        files = File.active_objects.filter(id__in=file_ids)
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
    def get_media_files_as_images(self, media_ids: list, user_id: int) -> list:
        """
        Convert media file IDs to image format for LLM vision API.
        Reads media files from disk and converts to base64 data URLs.

        Args:
            media_ids: List of media file IDs
            user_id: User ID for filtering

        Returns:
            List of dicts with 'preview' (base64 data URL), 'name', 'type'
        """
        if not media_ids:
            return []

        media_images = []

        # Fetch media files from database
        media_files = File.active_objects.filter(
            id__in=media_ids,
            user_id=user_id,
            is_media=True  # Only media files
        )

        for media_file in media_files:
            try:
                # Read file from disk
                with media_file.file.open('rb') as f:
                    file_data = f.read()

                # Convert to base64
                base64_data = base64.b64encode(file_data).decode('utf-8')

                # Create data URL
                data_url = f"data:{media_file.file_type};base64,{base64_data}"

                media_images.append({
                    'preview': data_url,
                    'name': media_file.name or media_file.file.name,
                    'type': media_file.file_type
                })
            except Exception as e:
                logger.error(f"Error reading media file {media_file.id}: {str(e)}")
                continue

        return media_images

    async def add_video_transcriptions_to_context(
        self,
        media_items: List[Dict],
        messages: List[Dict],
        user=None
    ) -> List[Dict]:
        """
        Add video transcriptions to message context for LLMs.

        Extracts and transcribes audio from videos, then adds the transcriptions
        to the message context so LLMs have access to the spoken content.

        Args:
            media_items: List of media dicts with 'preview', 'type', 'name'
            messages: List of message dictionaries
            user: Optional user for API key resolution

        Returns:
            Updated messages list with video transcriptions added
        """
        # Separate videos from images
        videos = [item for item in media_items if item.get('type', '').startswith('video/')]

        if not videos:
            return messages

        try:
            # Initialize Whisper service
            if user:
                api_key = await get_provider_api_key_for_user(Provider.OPENAI.value, user)
            else:
                api_key = await get_provider_api_key(Provider.OPENAI.value)

            whisper_service = WhisperService(api_key=api_key)

            # Transcribe all videos
            transcriptions = await whisper_service.transcribe_multiple_videos(videos)

            # Filter out failed transcriptions and build context
            successful_transcriptions = []
            for video_name, transcription in transcriptions.items():
                if transcription:
                    successful_transcriptions.append(
                        f"Video '{video_name}' audio transcription:\n{transcription}"
                    )

            # Add transcriptions to messages before the last user message
            if successful_transcriptions:
                transcription_context = (
                    "=== Video Audio Transcriptions ===\n\n"
                    + "\n\n".join(successful_transcriptions)
                    + "\n\n=== End of Video Transcriptions ===\n"
                )

                # Find last user message and insert transcription before it
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        messages.insert(i, {
                            "role": "user",
                            "content": transcription_context
                        })
                        break

                logger.info(f"Added transcriptions for {len(successful_transcriptions)} video(s)")

        except Exception as e:
            logger.error(f"Error transcribing videos: {str(e)}")
            # Don't fail the entire request if transcription fails

        return messages

    @database_sync_to_async
    def get_referenced_conversations_context(self, conversation_ids: list, user_id: int, history_limit: int = None) -> str:
        """Fetch context from referenced conversations.

        Args:
            conversation_ids: List of conversation IDs to fetch
            user_id: User ID for filtering
            history_limit: Optional limit for messages (None = all messages)
        """
        if not conversation_ids:
            return ""

        context_parts = []
        conversations = Conversation.active_objects.filter(
            conversation_id__in=conversation_ids,
            user_id=user_id
        )

        for conversation in conversations:
            messages_query = Message.active_objects.filter(
                conversation=conversation
            ).order_by('-created_at')

            if history_limit is not None:
                messages_query = messages_query[:history_limit]

            messages = list(messages_query)

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

    async def _get_ai_service(self, llm: LLM, user=None) -> AIService:
        """
        Get the appropriate AI service for the given LLM.
        Fetches API key asynchronously based on user's billing mode.

        Args:
            llm: The LLM model to use
            user: Optional user instance. If provided, uses user-specific key resolution
                  based on billing_mode. If None, falls back to system keys.
        """
        # Use user-aware key resolution if user is provided
        if user:
            api_key = await get_provider_api_key_for_user(llm.provider, user)
        else:
            api_key = await get_provider_api_key(llm.provider)

        if llm.provider == Provider.OPENAI.value:
            return OpenAIService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.CLAUDE.value:
            return ClaudeService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.GEMINI.value:
            return GeminiService(llm=llm, api_key=api_key)
        elif llm.provider == Provider.LLAMA.value:
            return LlamaService(llm=llm, api_key=api_key)
        return ClaudeService(llm=llm, api_key=api_key)

    def _get_web_search_tools(self, llm: LLM) -> list:
        """Get web search tools based on the LLM provider.

        All three providers (OpenAI, Claude, Gemini) support native web search.
        """
        provider_tools = {
            Provider.OPENAI.value: OpenAIService.get_web_search_tool,
            Provider.CLAUDE.value: ClaudeService.get_web_search_tool,
            Provider.GEMINI.value: GeminiService.get_web_search_tool,
        }

        tool_func = provider_tools.get(llm.provider)
        return [tool_func()] if tool_func else []

    # -------- SocraticBooks helpers --------
    async def _build_socratic_messages(
        self,
        message: str,
        conversation: 'Conversation',
        user_id: int,
        file_ids: list,
        embedding_ids: list,
        tag_ids: list,
        folder_ids: list,
        history_limit: int,
        max_context_snippets: int,
        document_similarity_threshold: float,
        message_obj: Message,
        workflow_run_step_obj,
        bot_meta: Dict,
    ) -> list:
        """Build messages array in the classic SocraticBooks format."""
        subject = (bot_meta or {}).get("subject", "")
        topic = (bot_meta or {}).get("topic", "")
        learning_goals = (bot_meta or {}).get("learning_goals", "No specific learning goals defined.")
        chat_prompt = (bot_meta or {}).get("chat_prompt", "Provide a helpful, educational response.")

        # System prompt
        prompt_start = (
            f"Subject and Topic:\n"
            f"Your job is to act as a living Socratic book that helps '{subject}' students\n"
            f"learn about different subjects. This chapter specifically is about '{topic}'."
        )
        system_prompt = (
            prompt_start
            + "\n\nTeaching Style:\n" + chat_prompt
            + "\n\nLearning Goals:\n" + learning_goals
        )

        # Conversation history as simple transcript
        history_list = await self.get_conversation_history(conversation, limit=history_limit) if conversation else []
        transcript_parts = []
        for h in history_list:
            role_name = "User" if h["role"] == "user" else "Assistant"
            content = (h["content"] or "").strip()
            if content:
                transcript_parts.append(f"{role_name}: {content}")
        conversation_history_text = "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."

        file_context_parts = []

        # Retrieve contextual snippets using embedding_ids (avoid full file reads)
        if embedding_ids:
            if user_id and user_id != self.document_processor.user_id:
                self.document_processor.user_id = user_id
                self.document_processor.vector_service = await get_vector_service_async(user_id)

            context = await self.document_processor.search_similar_documents(
                query_text=message,
                file_ids=embedding_ids,
                user_id=user_id,
                top_k=max_context_snippets,
                similarity_threshold=document_similarity_threshold,
                message_obj=message_obj,
                workflow_run_step_obj=workflow_run_step_obj
            )
            # print("context", context)
            if context:
                for part in context.split("\n\n"):
                    if part.strip():
                        file_context_parts.append(part)

        file_context_text = "\n\n".join([p for p in file_context_parts if p and p.strip()])
        if not file_context_text:
            file_context_text = "No relevant file content found."

        # User message assembled like the old SocraticBooks format
        user_message = (
            "Respond based on the following documents.\n"
            f"{file_context_text}\n"
            "And the recent conversation history:\n"
            f"{conversation_history_text}\n"
            f"Question: {message}\n"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    # -------- Advanced Prompt helpers --------
    async def _build_advanced_messages(
        self,
        message: str,
        conversation: 'Conversation',
        user_id: int,
        file_ids: list,
        embedding_ids: list,
        tag_ids: list,
        folder_ids: list,
        history_limit: int,
        max_context_snippets: int,
        document_similarity_threshold: float,
        message_obj: Message,
        workflow_run_step_obj,
        bot_meta: Dict,
    ) -> list:
        """Build messages using the Advanced Prompt construction provided."""
        title = (bot_meta or {}).get("title") or (conversation.title if conversation and conversation.title else "Untitled Conversation")
        subject = (bot_meta or {}).get("subject", "")
        topic = (bot_meta or {}).get("topic", "")
        learning_goals = (bot_meta or {}).get("learning_goals", "No specific learning goals defined.")
        chat_prompt = (bot_meta or {}).get("chat_prompt", "Provide a helpful, educational response.")

        # Conversation history as a readable transcript
        history_list = await self.get_conversation_history(conversation, limit=history_limit) if conversation else []
        transcript_parts = []
        for h in history_list:
            role_name = "User" if h["role"] == "user" else "Assistant"
            content = (h["content"] or "").strip()
            if content:
                transcript_parts.append(f"{role_name}: {content}")
        conversation_history_text = "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."

        # Build relevant content using embedding-based retrieval (avoid full file reads)
        relevant_sections = []

        if embedding_ids:
            if user_id and user_id != self.document_processor.user_id:
                self.document_processor.user_id = user_id
                self.document_processor.vector_service = await get_vector_service_async(user_id)

            context = await self.document_processor.search_similar_documents(
                query_text=message,
                file_ids=embedding_ids,
                user_id=user_id,
                top_k=max_context_snippets,
                similarity_threshold=document_similarity_threshold,
                message_obj=message_obj,
                workflow_run_step_obj=workflow_run_step_obj
            )
            # print("context", len(context))
            if context:
                for part in context.split("\n\n"):
                    if part.strip():
                        relevant_sections.append(part)

        relevant_content_text = "\n\n".join([s for s in relevant_sections if s and s.strip()])
        if not relevant_content_text:
            relevant_content_text = "No relevant external content found."

        # Assemble the advanced system prompt exactly as requested
        system_prompt = (
            f"Here is a conversation:\n{conversation_history_text}\n\n"
            f"This is a conversation on {title} (Subject: {subject}, Topic: {topic}).\n"
            f"We are trying to teach the following learning goals:\n{learning_goals}\n\n"
            f"{relevant_content_text}\n"
            f"The latest user message was: \"{message}\"\n\n"
            f"Please respond according to these directions:\n{chat_prompt}"
        )

        # Include the user message as a separate turn to comply with chat APIs
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]
