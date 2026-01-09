from abc import ABC, abstractmethod
from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from conversations.services.audio_transcription_service import AudioTranscriptionService
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
from core.services.vector_service import get_vector_service_async
from datetime import datetime
import logging

from core.services.llm_helpers import (
    build_transcription_context,
    insert_context_before_last_user_message,
    # Database helpers
    get_prompt,
    get_conversation_history,
    get_files_from_tags,
    get_files_from_folders,
    get_audio_or_video_files,
    get_full_file_contents,
    get_media_files_as_images,
    get_referenced_conversations_context,
    convert_file_to_base64_dict,
    # Socratic message builders
    build_classic_socratic_messages,
    build_advanced_socratic_messages,
)

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
        tools: Optional[List] = None,
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Generate AI response with context using DTO-based request.

        This is the main orchestrator method that delegates to specialized methods.

        Args:
            request: LLMQueryRequest containing all query parameters
            tools: Optional list of tool definitions to pass to the LLM

        Yields:
            Tuple of (chunk: str, usage: Dict) for streaming responses
        """
        try:
            llm = await self._resolve_llm(request)
            messages = await self._build_messages_for_request(request, llm)
            all_images = await self._process_media_files(request)

            if request.requires_audio_transcription():
                async for chunk, usage in self._execute_audio_transcription(request, llm):
                    yield chunk, usage
            elif request.requires_image_generation():
                async for chunk, usage in self._execute_image_generation(request, llm):
                    yield chunk, usage
            else:
                async for chunk, usage in self._execute_llm_completion(
                    request, llm, messages, all_images, tools=tools
                ):
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
        is_socratic = request.is_socratic_mode()
        logger.info(
            f"[LLMService] Building messages: "
            f"socratic_mode={is_socratic}, "
            f"socratic.enabled={request.socratic.enabled}, "
            f"is_advanced={request.is_advanced_mode()}"
        )

        if is_socratic:
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

        prompt = await get_prompt(request.generation.prompt_id)
        if prompt and prompt.strip():
            messages.append({"role": "assistant", "content": f"Prompt: {prompt}"})

        if request.context.referenced_conversation_ids:
            referenced_context = await get_referenced_conversations_context(
                request.context.referenced_conversation_ids,
                request.user.id if request.user else None,
                None
            )
            if referenced_context:
                messages.append({"role": "user", "content": referenced_context})

        if request.context.file_ids:
            file_contents = await get_full_file_contents(request.context.file_ids, self.file_processor)
            if file_contents:
                for file_content in file_contents:
                    messages.append({"role": "user", "content": file_content})

        await self._add_semantic_context_to_messages(request, messages)

        conversation_history = await get_conversation_history(
            request.conversation,
            limit=request.context.history_limit
        ) if request.conversation else []
        messages.extend([msg for msg in conversation_history if msg["content"].strip()])

        messages.append({"role": "user", "content": f"User's message: {request.message}"})

        return messages

    async def _collect_embedding_file_ids(self, request: LLMQueryRequest) -> set:
        """
        Collect all file IDs for embedding search from various sources.
        
        Aggregates file IDs from:
        - Direct embedding_ids
        - Files associated with tag_ids
        - Files in folder_ids
        
        Args:
            request: LLMQueryRequest with context config
            
        Returns:
            Set of file IDs to search for embeddings
        """
        all_file_ids = set(request.context.embedding_ids or [])
        user_id = request.user.id if request.user else None
        
        if request.context.tag_ids:
            tagged_file_ids = await get_files_from_tags(request.context.tag_ids, user_id)
            all_file_ids.update(tagged_file_ids)
        
        if request.context.folder_ids:
            folder_file_ids = await get_files_from_folders(request.context.folder_ids, user_id)
            all_file_ids.update(folder_file_ids)
        
        return all_file_ids

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

        all_embedding_file_ids = await self._collect_embedding_file_ids(request)
        if not all_embedding_file_ids:
            return

        user_id = request.user.id if request.user else None

        # Use file_owner_id from frontend (DARE user ID) for shared boards, fallback to current user
        vector_user_id = request.context.file_owner_id or user_id

        logger.info(f"VECTOR CONTEXT: user_id={user_id}, file_owner_id={request.context.file_owner_id}, vector_user_id={vector_user_id}")
        logger.info(f"VECTOR CONTEXT: file_ids={list(all_embedding_file_ids)}")

        if vector_user_id and vector_user_id != self.document_processor.user_id:
            self.document_processor.user_id = vector_user_id
            self.document_processor.vector_service = await get_vector_service_async(vector_user_id)

        effective_threshold = (
            0.05 if request.is_socratic_mode()
            else request.context.document_similarity_threshold
        )

        context = await self.document_processor.search_similar_documents(
            query_text=request.message,
            file_ids=list(all_embedding_file_ids),
            user_id=vector_user_id,  # Use file_owner_id for shared boards
            top_k=request.context.max_context_snippets,
            similarity_threshold=effective_threshold,
            message_obj=request.message_obj,
            workflow_run_step_obj=request.workflow_run_step_obj
        )

        if context and context.strip():
            messages.append({"role": "user", "content": f"Relevant context from documents:\n{context}"})

    async def _process_media_files(self, request: LLMQueryRequest) -> List[Dict]:
        """Process and combine all media files (images and videos).

        Args:
            request: LLMQueryRequest with media config

        Returns:
            List of processed image dictionaries (images and videos combined)
        """
        # Skip processing media as images if this is an audio transcription request
        # Audio files should only be processed by the transcription service
        if request.requires_audio_transcription():
            return request.media.images or []

        all_images = request.media.images or []

        if request.media.media_ids:
            user_id = request.user.id if request.user else None
            if user_id:
                media_images = await get_media_files_as_images(
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

    async def _execute_audio_transcription(
        self,
        request: LLMQueryRequest,
        llm: LLM
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Execute audio transcription request with streaming support.

        For large files that get split into chunks, this yields each chunk's
        transcription as it completes, allowing real-time progress updates.

        Args:
            request: LLMQueryRequest with audio transcription settings
            llm: LLM model (must be Whisper or support audio transcription)

        Yields:
            Tuple of (chunk: str, usage: Dict) where usage contains:
            - For intermediate chunks: {"transcription_chunk": {...}}
            - For final chunk: {"transcription_result": {...}}
        """
        # Get audio/video files from media_ids
        media_files = await get_audio_or_video_files(request.context.media_ids)

        if not media_files:
            yield "Error: No audio or video files found. Please upload an audio/video file to transcribe.", None
            return

        settings = request.generation.audio_transcription_settings or {}
        language = settings.get("language", "auto")
        # Convert 'auto' to None for Whisper API
        language = None if language == "auto" else language
        # Check if streaming is enabled (default True for new behavior)
        enable_streaming = settings.get("stream_chunks", True)

        # Transcribe each audio/video file
        accumulated_text = ""
        final_transcription = None

        for media_file in media_files:
            try:
                if enable_streaming:
                    # Use streaming transcription - yields chunks as they complete
                    file_name = media_file.name
                    chunk_texts = []
                    transcription_error = None

                    async for chunk_data in AudioTranscriptionService.transcribe_audio_file_streaming(
                        file_obj=media_file,
                        language=language,
                        model=llm.identifier
                    ):
                        # Check if this is an error response
                        if chunk_data.get("error"):
                            transcription_error = chunk_data.get("error_message", "Unknown transcription error")
                            logger.error(f"Transcription error for {file_name}: {transcription_error}")
                            break

                        chunk_text = chunk_data["text"]
                        chunk_texts.append(chunk_text)

                        # Build formatted text progressively
                        if chunk_data["chunk_index"] == 0:
                            # First chunk - add header
                            accumulated_text = f"**Transcription of `{file_name}`**\n\n{chunk_text}"
                        else:
                            # Subsequent chunks - append with space
                            accumulated_text += " " + chunk_text

                        # Yield immediately after each chunk (this is the key fix!)
                        yield accumulated_text, None

                    # If there was an error, yield it and return
                    if transcription_error:
                        yield f"Error transcribing {file_name}: {transcription_error}", None
                        return

                    # Build final transcription result after all chunks
                    if chunk_texts:
                        final_transcription = {
                            'text': " ".join(chunk_texts),
                            'language': language or 'auto',
                            'model': llm.identifier,
                            'file_id': media_file.id,
                            'file_name': media_file.name,
                            'file_size': media_file.size,
                            'media_type': media_file.media_type,
                            'transcribed_at': datetime.now().isoformat(),
                        }
                else:
                    # Use original non-streaming transcription
                    final_transcription = await AudioTranscriptionService.transcribe_audio_file(
                        file_obj=media_file,
                        language=language,
                        model=llm.identifier
                    )

            except Exception as e:
                logger.exception(f"Error transcribing media file {media_file.id}: {str(e)}")
                yield f"Error transcribing {media_file.name}: {str(e)}", None
                return

        # Yield final result with usage data
        if final_transcription:
            result_text = AudioTranscriptionService.format_transcription_for_display(final_transcription)

            # Yield the final transcription text and usage data
            usage_data = final_transcription.copy()
            usage_data["transcription_result"] = final_transcription

            yield result_text, usage_data
        else:
            file_names = ", ".join([f.name for f in media_files]) if media_files else "unknown"
            yield f"Error: Transcription failed for {file_names}. Please check the server logs for more details.", None

    async def _execute_llm_completion(
        self,
        request: LLMQueryRequest,
        llm: LLM,
        messages: List[Dict[str, str]],
        all_images: List[Dict],
        tools: Optional[List] = None,
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
        
        # Use provided tools, or web search tools if enabled
        llm_tools = tools
        if not llm_tools and request.requires_web_search():
            llm_tools = self._get_web_search_tools(llm)

        if request.generation.structured_spec:
            # Structured output (non-streaming)
            logger.info(
                f"[LLMService] Using structured output with provider: {llm.provider}, "
                f"model: {llm.identifier}, spec: {request.generation.structured_spec}"
            )
            text = await ai_service.get_chat_completion(
                messages,
                request.generation.max_tokens,
                request.generation.temperature,
                structured_spec=request.generation.structured_spec
            )
            logger.info(f"[LLMService] Structured output response received: {text[:200]}...")
            yield text, None
        else:
            # Standard streaming completion
            async for chunk, usage in ai_service.stream_chat_completion(
                messages,
                request.generation.max_tokens,
                request.generation.temperature,
                images=all_images,
                tools=llm_tools
            ):
                yield chunk, usage

    # ========== End Query Orchestration Methods ==========

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

            # Build and insert transcription context
            context = build_transcription_context(transcriptions)
            if context:
                insert_context_before_last_user_message(messages, context)
                logger.info(f"Added transcriptions for {len([t for t in transcriptions.values() if t])} video(s)")

        except Exception as e:
            logger.error(f"Error transcribing videos: {str(e)}")
            # Don't fail the entire request if transcription fails

        return messages


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
    async def _build_socratic_messages(self, context: MessageBuildContext) -> list:
        """Build messages in classic SocraticBooks format."""
        return await build_classic_socratic_messages(context, self.document_processor)

    async def _build_advanced_messages(self, context: MessageBuildContext) -> list:
        """Build messages in advanced SocraticBooks format."""
        return await build_advanced_socratic_messages(context, self.document_processor)
