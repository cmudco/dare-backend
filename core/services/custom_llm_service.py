"""
Custom LLM service implementation for OpenAI-compatible endpoints.

This service enables integration with custom LLM endpoints that follow the OpenAI API specification,
including LiteLLM proxy servers and other OpenAI-compatible providers.
"""
import httpx
import logging
from typing import AsyncGenerator, List, Dict, Tuple, Optional

from openai import AsyncOpenAI

from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key
from core.services.llm_utils import (
    OpenAIMessageFormatter,
    OpenAIVisionHandler,
    OpenAIErrorHandler,
    OpenAIStreamProcessor,
    StreamAggregator,
)

logger = logging.getLogger(__name__)


class CustomLLMService:
    """Service for interacting with custom OpenAI-compatible LLM endpoints."""

    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize Custom LLM service with OpenAI-compatible endpoint.

        Args:
            llm: LLM model instance with configuration including base_url
            api_key: Optional API key override. If not provided, uses provider key resolution
        """
        # Validate that base_url is provided for custom endpoints
        if not llm.base_url:
            raise ValueError(
                f"Custom LLM '{llm.name}' requires a base_url to be configured. "
                "Please set the base_url field in the Django admin."
            )

        # Use provided key or fetch from provider key service
        if api_key is None:
            api_key = get_provider_api_key(llm.provider)

        # Initialize OpenAI client with custom base URL
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=llm.base_url,
            http_client=httpx.AsyncClient(verify=False),
        )
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning
        self.base_url = llm.base_url

        logger.info(f"Initialized CustomLLMService for {llm.name} at {llm.base_url}")

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        images: List[Dict] = None,
        tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Stream chat completions from custom OpenAI-compatible endpoint.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            images: List of image dicts for vision support (if supported by endpoint)
            tools: Optional tools list (not commonly supported by custom endpoints)

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        try:
            # Prepare messages with vision if needed
            prepared_messages = self._prepare_messages(messages, images)

            # Stream chat completions
            response = await self._stream_chat_completions(
                prepared_messages,
                max_tokens,
                temperature
            )

            # Process and yield stream chunks
            processor = OpenAIStreamProcessor.process_chat_completion_stream
            async for chunk, usage in processor(response):
                yield chunk, usage

        except Exception as e:
            logger.exception(f"Custom LLM streaming error for {self.base_url}")
            error_message = OpenAIErrorHandler.format_error(e)
            yield f"Error: {error_message}", None

    async def get_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        structured_spec: Optional[Dict] = None,
    ) -> str:
        """
        Get a complete (non-streaming) chat completion.

        Note: Structured outputs may not be supported by all custom endpoints.

        Args:
            messages: List of message dictionaries
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            structured_spec: Optional schema specification (may not be supported)

        Returns:
            Complete generated response text
        """
        # Default: use streaming and aggregate
        # Custom endpoints may not support structured outputs
        stream = self.stream_chat_completion(messages, max_tokens, temperature)
        return await StreamAggregator.aggregate_stream(stream)

    # ==================== Private Methods ====================

    def _prepare_messages(
        self,
        messages: List[Dict],
        images: Optional[List[Dict]]
    ) -> List[Dict]:
        """
        Prepare messages by adding vision content if needed.

        Args:
            messages: Original messages
            images: Optional images to add

        Returns:
            Messages with vision content added (if supported)
        """
        if not images:
            return messages

        # Vision support depends on the custom endpoint
        # Use OpenAI's vision handler format
        return OpenAIVisionHandler.add_images_to_messages(messages, images)

    async def _stream_chat_completions(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float
    ):
        """
        Stream using Chat Completions API.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting

        Returns:
            OpenAI Chat Completions stream
        """
        kwargs = self._build_chat_completion_params(
            messages,
            max_tokens,
            temperature
        )

        return await self.client.chat.completions.create(**kwargs)

    def _build_chat_completion_params(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float
    ) -> Dict:
        """
        Build parameters for chat completion API call.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting

        Returns:
            Parameters dictionary
        """
        params = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Reasoning models use different parameter names
        if self.is_reasoning:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
            params["temperature"] = temperature

        return params

    # ==================== Static Methods ====================

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Web search tool for custom endpoints.

        Note: Most custom endpoints don't support web search.
        Returns empty dict to indicate no web search support.
        """
        return {}
