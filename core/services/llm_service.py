from abc import ABC, abstractmethod
from channels.db import database_sync_to_async
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.custom_llm_service import CustomLLMService
from core.services.file_processor import FileProcessor
from core.services.whisper_service import WhisperService
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.dtos import LLMQueryRequest, LLMQueryChunk, MessageBuildContext
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
        request: LLMQueryRequest,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Generate AI response with context using DTO-based request.

        This is the main orchestrator method that delegates to specialized methods.

        Args:
            request: LLMQueryRequest containing all query parameters

        Yields:
            Tuple of (chunk: str, usage: Dict) for streaming responses
        """
        try:
            llm = await self._resolve_llm(request)
            messages = await self._build_messages_for_request(request, llm)
            all_images = await self._process_media_files(request)

            if request.requires_image_generation():
                async for chunk, usage in self._execute_image_generation(request, llm):
                    yield chunk, usage
            else:
                async for chunk, usage in self._execute_llm_completion(request, llm, messages, all_images):
                    yield chunk, usage

        except Exception as e:
            yield f"Error: {str(e)}", None

    # ========== Query Orchestration Methods ==========

    async def _resolve_llm(self, request: LLMQueryRequest) -> LLM:
        """Resolve the LLM model to use for this request.

        Args:
            request: LLMQueryRequest containing optional LLM

        Returns:
            LLM model instance

        Raises:
            ValueError: If no LLM is found
        """
        llm = request.llm or LLM.objects.filter(is_active=True).first()
        if not llm:
            raise ValueError("No active LLM found")
        return llm

    async def _build_messages_for_request(
        self,
        request: LLMQueryRequest,
        llm: LLM
    ) -> List[Dict[str, str]]:
        """Build messages array based on request type (Socratic vs Standard).

        Args:
            request: LLMQueryRequest containing message and context
            llm: Resolved LLM model

        Returns:
            List of message dictionaries for LLM
        """
        if request.is_socratic_mode():
            return await self._build_socratic_mode_messages(request)
        else:
            return await self._build_standard_messages(request)

    async def _build_socratic_mode_messages(
        self,
        request: LLMQueryRequest
    ) -> List[Dict[str, str]]:
        """Build messages for Socratic teaching mode.

        Args:
            request: LLMQueryRequest with Socratic config

        Returns:
            List of message dictionaries
        """
        build_context = MessageBuildContext.from_request(request)

        if request.is_advanced_mode():
            return await self._build_advanced_messages(build_context)
        else:
            return await self._build_socratic_messages(build_context)

    async def _build_standard_messages(
        self,
        request: LLMQueryRequest
    ) -> List[Dict[str, str]]:
        """Build messages for standard (non-Socratic) mode.

        Args:
            request: LLMQueryRequest with context and files

        Returns:
            List of message dictionaries
        """
        messages = []

        prompt = await self.get_prompt(request.generation.prompt_id)
        if prompt and prompt.strip():
            messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

        if request.context.referenced_conversation_ids:
            referenced_context = await self.get_referenced_conversations_context(
                request.context.referenced_conversation_ids,
                request.user.id if request.user else None,
                None
            )
            if referenced_context:
                messages.append({"role": "user", "content": referenced_context})

        if request.context.file_ids:
            file_contents = await self.get_full_file_contents(request.context.file_ids)
            if file_contents:
                for file_content in file_contents:
                    messages.append({"role": "user", "content": file_content})

        await self._add_semantic_context_to_messages(request, messages)

        conversation_history = await self.get_conversation_history(
            request.conversation,
            limit=request.context.history_limit
        ) if request.conversation else []
        messages.extend([msg for msg in conversation_history if msg["content"].strip()])

        messages.append({"role": "user", "content": f"User's message: {request.message}"})

        return messages

    async def _add_semantic_context_to_messages(
        self,
        request: LLMQueryRequest,
        messages: List[Dict[str, str]]
    ) -> None:
        """Add semantic search results to messages array.

        Args:
            request: LLMQueryRequest with context config
            messages: Messages list to append to (modified in place)
        """
        if not (request.context.embedding_ids or request.context.tag_ids or request.context.folder_ids):
            return

        all_embedding_file_ids = set(request.context.embedding_ids or [])
        user_id = request.user.id if request.user else None

        if request.context.tag_ids:
            tagged_file_ids = await self.get_files_from_tags(request.context.tag_ids, user_id)
            all_embedding_file_ids.update(tagged_file_ids)

        if request.context.folder_ids:
            folder_file_ids = await self.get_files_from_folders(request.context.folder_ids, user_id)
            all_embedding_file_ids.update(folder_file_ids)

        if not all_embedding_file_ids:
            return

        if user_id and user_id != self.document_processor.user_id:
            self.document_processor.user_id = user_id
            self.document_processor.vector_service = await get_vector_service_async(user_id)

        effective_threshold = (
            0.05 if request.is_socratic_mode()
            else request.context.document_similarity_threshold
        )

        context = await self.document_processor.search_similar_documents(
            query_text=request.message,
            file_ids=list(all_embedding_file_ids),
            user_id=user_id,
            top_k=request.context.max_context_snippets,
            similarity_threshold=effective_threshold,
            message_obj=request.message_obj,
            workflow_run_step_obj=request.workflow_run_step_obj
        )

        if context:
            for part in context.split("\n\n"):
                if part.strip():
                    messages.append({"role": "user", "content": part})

    async def _process_media_files(self, request: LLMQueryRequest) -> List[Dict]:
        """Process and combine all media files (images and videos).

        Args:
            request: LLMQueryRequest with media config

        Returns:
            List of processed image dictionaries (images and videos combined)
        """
        all_images = request.media.images or []

        if request.media.media_ids:
            user_id = request.user.id if request.user else None
            if user_id:
                media_images = await self.get_media_files_as_images(
                    request.media.media_ids,
                    user_id
                )
                all_images = all_images + media_images

        return all_images

    async def _execute_image_generation(
        self,
        request: LLMQueryRequest,
        llm: LLM
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Execute image generation request.

        Args:
            request: LLMQueryRequest with image generation settings
            llm: LLM model (must be DALL-E)

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        ai_service = await self._get_ai_service(llm, request.user)

        # Extract DALL-E model from LLM identifier
        model = llm.identifier if llm.identifier in ["dall-e-3", "dall-e-2"] else "dall-e-3"

        settings = request.generation.image_generation_settings or {}
        size = settings.get("size")
        quality = settings.get("quality")
        style = settings.get("style")

        async for chunk, usage in ai_service.generate_image(
            prompt=request.message,
            model=model,
            size=size,
            quality=quality,
            style=style
        ):
            yield chunk, usage

    async def _execute_llm_completion(
        self,
        request: LLMQueryRequest,
        llm: LLM,
        messages: List[Dict[str, str]],
        all_images: List[Dict]
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Execute LLM completion (streaming or structured).

        Args:
            request: LLMQueryRequest with generation config
            llm: Resolved LLM model
            messages: Built messages array
            all_images: Processed media files

        Yields:
            Tuple of (chunk: str, usage: Dict)
        """
        if all_images:
            messages = await self.add_video_transcriptions_to_context(
                all_images,
                messages,
                request.user
            )

        ai_service = await self._get_ai_service(llm, request.user)
        tools = self._get_web_search_tools(llm) if request.requires_web_search() else None

        if request.generation.structured_spec:
            # Structured output (non-streaming)
            text = await ai_service.get_chat_completion(
                messages,
                request.generation.max_tokens,
                request.generation.temperature,
                structured_spec=request.generation.structured_spec
            )
            yield text, None
        else:
            # Standard streaming completion
            async for chunk, usage in ai_service.stream_chat_completion(
                messages,
                request.generation.max_tokens,
                request.generation.temperature,
                images=all_images,
                tools=tools
            ):
                yield chunk, usage

    # ========== End Query Orchestration Methods ==========

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
        elif llm.provider == Provider.CUSTOM.value:
            return CustomLLMService(llm=llm, api_key=api_key)
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
        context: MessageBuildContext,
    ) -> list:
        """Build messages array in the classic SocraticBooks format.

        Args:
            context: MessageBuildContext with all necessary data

        Returns:
            List of message dictionaries for LLM
        """
        subject = context.subject
        topic = context.topic
        learning_goals = context.learning_goals
        chat_prompt = context.chat_prompt

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
        history_list = await self.get_conversation_history(
            context.conversation,
            limit=context.history_limit
        ) if context.conversation else []
        transcript_parts = []
        for h in history_list:
            role_name = "User" if h["role"] == "user" else "Assistant"
            content = (h["content"] or "").strip()
            if content:
                transcript_parts.append(f"{role_name}: {content}")
        conversation_history_text = "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."

        file_context_parts = []

        # Retrieve contextual snippets using embedding_ids (avoid full file reads)
        if context.embedding_ids:
            if context.user_id and context.user_id != self.document_processor.user_id:
                self.document_processor.user_id = context.user_id
                self.document_processor.vector_service = await get_vector_service_async(context.user_id)

            doc_context = await self.document_processor.search_similar_documents(
                query_text=context.message,
                file_ids=context.embedding_ids,
                user_id=context.user_id,
                top_k=context.max_context_snippets,
                similarity_threshold=context.document_similarity_threshold,
                message_obj=context.message_obj,
                workflow_run_step_obj=context.workflow_run_step_obj
            )
            # print("context", doc_context)
            if doc_context:
                for part in doc_context.split("\n\n"):
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
            f"Question: {context.message}\n"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    # -------- Advanced Prompt helpers --------
    async def _build_advanced_messages(
        self,
        context: MessageBuildContext,
    ) -> list:
        """Build messages using the Advanced Prompt construction provided.

        Args:
            context: MessageBuildContext with all necessary data

        Returns:
            List of message dictionaries for LLM
        """
        title = context.title or (context.conversation.title if context.conversation and context.conversation.title else "Untitled Conversation")
        subject = context.subject
        topic = context.topic
        learning_goals = context.learning_goals
        chat_prompt = context.chat_prompt

        # Conversation history as a readable transcript
        history_list = await self.get_conversation_history(
            context.conversation,
            limit=context.history_limit
        ) if context.conversation else []
        transcript_parts = []
        for h in history_list:
            role_name = "User" if h["role"] == "user" else "Assistant"
            content = (h["content"] or "").strip()
            if content:
                transcript_parts.append(f"{role_name}: {content}")
        conversation_history_text = "\n\n".join(transcript_parts) if transcript_parts else "No previous messages."

        # Build relevant content using embedding-based retrieval (avoid full file reads)
        relevant_sections = []

        if context.embedding_ids:
            if context.user_id and context.user_id != self.document_processor.user_id:
                self.document_processor.user_id = context.user_id
                self.document_processor.vector_service = await get_vector_service_async(context.user_id)

            doc_context = await self.document_processor.search_similar_documents(
                query_text=context.message,
                file_ids=context.embedding_ids,
                user_id=context.user_id,
                top_k=context.max_context_snippets,
                similarity_threshold=context.document_similarity_threshold,
                message_obj=context.message_obj,
                workflow_run_step_obj=context.workflow_run_step_obj
            )
            # print("context", len(doc_context))
            if doc_context:
                for part in doc_context.split("\n\n"):
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
            f"The latest user message was: \"{context.message}\"\n\n"
            f"Please respond according to these directions:\n{chat_prompt}"
        )

        # Include the user message as a separate turn to comply with chat APIs
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context.message},
        ]
