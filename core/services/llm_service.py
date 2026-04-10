import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Dict, Tuple, Optional, Any, List

from conversations.constants import Provider, SenderType
from conversations.models import LLM, Conversation, Message
from core.integrations import ToolFetcher
from core.services.document_processor import DocumentProcessor
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.custom_llm_service import CustomLLMService
from core.services.file_processor import FileProcessor
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.dtos import LLMQueryRequest, LLMQueryChunk
from files.models import File, Folder

from core.services.llm_helpers import (
    # Database helpers
    get_media_files_as_images,
    # Socratic message builders
    build_classic_socratic_messages,
    build_advanced_socratic_messages,
    # Media helpers
    add_video_transcriptions_to_messages,
    execute_audio_transcription,
    # Standard message builders
    build_standard_messages,
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
        self.tool_fetcher = ToolFetcher()

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
            self._pending_memory_context = []
            messages = await self._build_messages_for_request(request, llm)
            all_images = await self._process_media_files(request)

            # Collect all tools (MCP + DARE + any passed externally) via ToolFetcher
            all_tools = await self.tool_fetcher.get_all_tools(request, llm, tools)

            # Capture memory context to attach to final usage
            memory_context = self._pending_memory_context

            if request.requires_audio_transcription():
                async for chunk, usage in self._execute_audio_transcription(request, llm):
                    if usage and memory_context:
                        usage["memory_context"] = memory_context
                    yield chunk, usage
            elif request.requires_image_generation():
                async for chunk, usage in self._execute_image_generation(request, llm):
                    if usage and memory_context:
                        usage["memory_context"] = memory_context
                    yield chunk, usage
            else:
                async for chunk, usage in self._execute_llm_completion(
                    request, llm, messages, all_images, tools=all_tools if all_tools else None
                ):
                    if usage and memory_context:
                        usage["memory_context"] = memory_context
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
        if request.is_advanced_mode():
            return await build_advanced_socratic_messages(request, self.document_processor)
        else:
            return await build_classic_socratic_messages(request, self.document_processor)

    async def _build_standard_messages(self, request: LLMQueryRequest) -> List[Dict[str, str]]:
        """Build messages for standard (non-Socratic) mode."""
        result = await build_standard_messages(request, self.document_processor, self.file_processor)
        # Store memory context for inclusion in final usage data
        self._pending_memory_context = result.memory_context
        return result.messages

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

    async def _execute_audio_transcription(self, request: LLMQueryRequest, llm: LLM) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Execute audio transcription with streaming support."""
        async for chunk, usage in execute_audio_transcription(
            request.context.media_ids,
            llm.identifier,
            request.generation.audio_transcription_settings,
        ):
            yield chunk, usage

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

    async def add_video_transcriptions_to_context(self, media_items: List[Dict], messages: List[Dict], user=None) -> List[Dict]:
        """Add video transcriptions to message context for LLMs."""
        return await add_video_transcriptions_to_messages(media_items, messages, user)


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
