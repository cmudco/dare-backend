"""
OpenAI LLM service implementation.

This service provides a clean, readable interface for interacting with OpenAI's
GPT models, including support for streaming, vision, web search, and structured outputs.
"""

import json
import logging
from typing import AsyncGenerator, List, Dict, Tuple, Optional

from openai import AsyncOpenAI

from config import env
from conversations.models import LLM
from core.services.llm_utils import (
    OpenAIMessageFormatter,
    OpenAIVisionHandler,
    OpenAIErrorHandler,
    OpenAIStreamProcessor,
    OpenAIWebSearchTools,
    WebSearchTools,
    StreamAggregator,
    SchemaTransformer,
)

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for interacting with OpenAI's GPT models."""

    def __init__(self, llm: LLM):
        """
        Initialize OpenAI service.

        Args:
            llm: LLM model instance with configuration
        """
        self.client = AsyncOpenAI(api_key=env.OPENAI_API_KEY)
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        images: List[Dict] = None,
        tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Stream chat completions from OpenAI's GPT model.

        This is the main public method for streaming responses. It orchestrates
        the entire streaming process with clear separation of concerns.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            images: List of image dicts for vision support
            tools: Optional tools list for web search

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        try:
            # Step 1: Prepare messages with vision if needed
            prepared_messages = self._prepare_messages(messages, images)

            # Step 2: Create appropriate stream (web search vs regular)
            web_search_enabled = WebSearchTools.has_web_search(tools)

            if web_search_enabled:
                response = await self._stream_with_web_search(prepared_messages)
                processor = OpenAIStreamProcessor.process_responses_api_stream
            else:
                response = await self._stream_chat_completions(
                    prepared_messages,
                    max_tokens,
                    temperature
                )
                processor = OpenAIStreamProcessor.process_chat_completion_stream

            # Step 3: Process and yield stream chunks
            async for chunk, usage in processor(response):
                yield chunk, usage

        except Exception as e:
            logger.exception("OpenAI streaming error")
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

        This method handles both regular completions and structured outputs.

        Args:
            messages: List of message dictionaries
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            structured_spec: Optional schema specification for structured outputs

        Returns:
            Complete generated response text
        """
        if structured_spec:
            return await self._get_structured_completion(
                messages,
                structured_spec
            )

        # Default: use streaming and aggregate
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
            Messages with vision content added
        """
        if not images:
            return messages

        return OpenAIVisionHandler.add_images_to_messages(messages, images)

    async def _stream_with_web_search(self, messages: List[Dict[str, str]]):
        """
        Stream using Responses API with web search enabled.

        Args:
            messages: Prepared messages

        Returns:
            OpenAI Responses API stream
        """
        input_data = OpenAIMessageFormatter.format_for_responses_api(messages)

        return await self.client.responses.create(
            model=self.model,
            input=input_data,
            tools=[{"type": "web_search"}],
            stream=True
        )

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

    async def _get_structured_completion(
        self,
        messages: List[Dict],
        structured_spec: Dict
    ) -> str:
        """
        Get structured output using OpenAI response format.

        Args:
            messages: List of messages
            structured_spec: Schema specification

        Returns:
            Extracted field value as string
        """
        response_format = SchemaTransformer.transform_for_openai(structured_spec)

        if not response_format:
            # Fallback to regular completion
            stream = self.stream_chat_completion(messages)
            return await StreamAggregator.aggregate_stream(stream)

        # Generate with structured output
        response = await self._generate_with_structure(
            messages,
            response_format
        )

        # Extract and return field value
        return self._extract_field_value(response, structured_spec)

    async def _generate_with_structure(
        self,
        messages: List[Dict],
        response_format: Dict
    ):
        """
        Generate completion with response format.

        Args:
            messages: List of messages
            response_format: Response format specification

        Returns:
            OpenAI response
        """
        # Flatten to text-only for structured outputs
        input_data = OpenAIMessageFormatter.format_for_responses_api(messages)

        if not isinstance(input_data, str):
            # Has multimodal - flatten to text
            input_data = OpenAIMessageFormatter.flatten_to_text(messages)

        return await self.client.responses.create(
            model=self.model,
            input=input_data,
            response_format=response_format,
        )

    def _extract_field_value(self, response, structured_spec: Dict) -> str:
        """
        Extract field value from structured response.

        Args:
            response: OpenAI response object
            structured_spec: Schema specification with field name

        Returns:
            Extracted value as string
        """
        text_out = getattr(response, 'output_text', None)

        if not text_out:
            return ""

        try:
            data = json.loads(text_out)
            field_name = structured_spec.get('field', 'route')
            value = data.get(field_name)
            return str(value) if value is not None else text_out
        except Exception:
            return text_out

    # ==================== Static Methods ====================

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Get web search tool indicator for OpenAI.

        OpenAI uses the Responses API with tools=[{"type": "web_search"}].

        Returns:
            Web search tool dictionary
        """
        return OpenAIWebSearchTools.get_tool_definition()
