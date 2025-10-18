"""
Gemini LLM service implementation.

This service provides a clean, readable interface for interacting with Google's
Gemini AI models, including support for streaming, vision, web search, and
structured outputs.
"""

import logging
import asyncio
import json
from typing import AsyncGenerator, Dict, List, Tuple, Optional

from google import genai
from google.genai import types

from config import env
from conversations.models import LLM
from core.services.llm_utils import (
    GeminiMessageFormatter,
    GeminiVisionHandler,
    GeminiErrorHandler,
    GeminiStreamProcessor,
    GeminiWebSearchTools,
    StreamAggregator,
    SchemaTransformer,
)

logger = logging.getLogger(__name__)


class GeminiService:
    """Service for interacting with Google Gemini models."""

    def __init__(self, llm: LLM):
        """
        Initialize Gemini service.

        Args:
            llm: LLM model instance with configuration
        """
        self.client = genai.Client(api_key=env.GEMINI_API_KEY)
        self.model_identifier = llm.identifier
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
        Stream chat completions from Google Gemini API.

        This is the main public method for streaming responses. It orchestrates
        the entire streaming process with clear separation of concerns.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            images: List of image dicts for vision support
            tools: List of tools including google_search support

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        try:
            # Step 1: Prepare messages with vision if needed
            prepared_messages = self._prepare_messages(messages, images)

            # Step 2: Create streaming response
            response_stream = await self._create_stream(
                prepared_messages,
                max_tokens,
                temperature,
                tools
            )

            # Step 3: Process and yield stream chunks
            async for chunk, usage in GeminiStreamProcessor.process_stream(response_stream):
                yield chunk, usage

        except Exception as e:
            logger.error(f"Error in Gemini stream_chat_completion: {e}")
            error_message = GeminiErrorHandler.format_error(e)
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
                max_tokens,
                temperature,
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

        return GeminiVisionHandler.add_images_to_messages(messages, images)

    async def _create_stream(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]]
    ):
        """
        Create Gemini streaming response.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools configuration

        Returns:
            Gemini response stream
        """
        contents = GeminiMessageFormatter.convert_to_contents(messages)
        config = self._build_generation_config(max_tokens, temperature, tools)

        def generate_sync():
            return self.client.models.generate_content_stream(
                model=self.model_identifier,
                contents=contents,
                config=config,
            )

        return await asyncio.to_thread(generate_sync)

    def _build_generation_config(
        self,
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]]
    ) -> types.GenerateContentConfig:
        """
        Build Gemini generation configuration.

        Args:
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools configuration

        Returns:
            Gemini generation config
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        if GeminiWebSearchTools.has_google_search(tools):
            config.tools = [GeminiWebSearchTools.build_google_search_tool()]

        return config

    async def _get_structured_completion(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        structured_spec: Dict
    ) -> str:
        """
        Get structured output using Gemini response schema.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            structured_spec: Schema specification

        Returns:
            Extracted field value as string
        """
        response_mime_type, response_schema = SchemaTransformer.transform_for_gemini(
            structured_spec
        )

        if not response_mime_type or not response_schema:
            # Fallback to regular completion
            stream = self.stream_chat_completion(messages, max_tokens, temperature)
            return await StreamAggregator.aggregate_stream(stream)

        # Generate with schema
        response = await self._generate_with_schema(
            messages,
            max_tokens,
            temperature,
            response_mime_type,
            response_schema
        )

        # Extract and return field value
        return self._extract_field_value(response, structured_spec)

    async def _generate_with_schema(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        response_mime_type: str,
        response_schema: Dict
    ) -> str:
        """
        Generate content with response schema.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            response_mime_type: MIME type for response
            response_schema: Response schema

        Returns:
            Generated response
        """
        contents = GeminiMessageFormatter.convert_to_contents(messages)

        generation_config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type=response_mime_type,
            response_schema=response_schema
        )

        def generate_sync():
            return self.client.models.generate_content(
                model=self.model_identifier,
                contents=contents,
                config=generation_config,
            )

        return await asyncio.to_thread(generate_sync)

    def _extract_field_value(self, response, structured_spec: Dict) -> str:
        """
        Extract field value from structured response.

        Args:
            response: Gemini response object
            structured_spec: Schema specification with field name

        Returns:
            Extracted value as string
        """
        text_out = getattr(response, 'text', None)

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
        Get the native Google Search tool definition for Gemini API.

        Returns:
            Web search tool dictionary
        """
        return GeminiWebSearchTools.get_tool_definition()
