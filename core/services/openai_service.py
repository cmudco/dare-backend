"""
OpenAI LLM service implementation.

This service provides a clean, readable interface for interacting with OpenAI's
GPT models, including support for streaming, vision, web search, and structured outputs.
"""

import base64
import json
import logging
from decimal import Decimal
from typing import AsyncGenerator, List, Dict, Tuple, Optional

from openai import AsyncOpenAI

from config import env
from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key
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

    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize OpenAI service.

        Args:
            llm: LLM model instance with configuration
            api_key: Optional API key override. If not provided, uses provider key resolution
        """
        # Use provided key or fetch from provider key service
        if api_key is None:
            api_key = get_provider_api_key(llm.provider)

        self.api_key = api_key
        self._client = None
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning

    @property
    def client(self) -> AsyncOpenAI:
        """
        Lazy initialization of OpenAI client.

        This prevents issues with async HTTP clients in RQ background workers
        by creating the client on first use rather than during __init__.
        """
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

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
                # Filter out web_search tools before passing to chat completions
                non_web_tools = [t for t in (tools or []) if t.get("type") != "web_search"]
                response = await self._stream_chat_completions(
                    prepared_messages,
                    max_tokens,
                    temperature,
                    non_web_tools if non_web_tools else None
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
        temperature: float,
        tools: Optional[List[Dict]] = None
    ):
        """
        Stream using Chat Completions API.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools for function calling

        Returns:
            OpenAI Chat Completions stream
        """
        kwargs = self._build_chat_completion_params(
            messages,
            max_tokens,
            temperature,
            tools
        )

        return await self.client.chat.completions.create(**kwargs)

    def _build_chat_completion_params(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Build parameters for chat completion API call.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools for function calling

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

        # Add tools if provided (for function calling like artifacts)
        if tools:
            params["tools"] = tools

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
        logger.info(
            f"[OpenAI] _get_structured_completion called with model: {self.model}, "
            f"spec: {structured_spec}"
        )

        response_format = SchemaTransformer.transform_for_openai(structured_spec)
        logger.info(f"[OpenAI] Transformed response_format: {response_format}")

        if not response_format:
            # Fallback to regular completion
            logger.warning("[OpenAI] No response_format generated, falling back to streaming")
            stream = self.stream_chat_completion(messages)
            return await StreamAggregator.aggregate_stream(stream)

        try:
            logger.info("[OpenAI] Calling native structured output API with response_format")
            response = await self._generate_with_chat_completions_structure(
                messages,
                response_format
            )
            logger.info(f"[OpenAI] Native API response received: {str(response)[:200]}...")

            extracted_value = self._extract_field_value(response, structured_spec)
            logger.info(f"[OpenAI] Extracted field value: {extracted_value}")
            return extracted_value
        except Exception as e:
            logger.error(f"OpenAI structured output error: {str(e)}", exc_info=True)
            error_message = OpenAIErrorHandler.format_error(e)
            return f"Error: {error_message}"

    async def _generate_with_chat_completions_structure(
        self,
        messages: List[Dict],
        response_format: Dict
    ):
        """
        Generate completion with response format using chat.completions API.
        
        Note: Structured outputs must use chat.completions.create(), not responses.create()
        The responses API doesn't support the response_format parameter.

        Args:
            messages: List of messages
            response_format: Response format specification

        Returns:
            OpenAI response with structured output
        """
        params = {
            "model": self.model,
            "messages": messages,
            "response_format": response_format,
        }
        
        if self.is_reasoning:
            params["max_completion_tokens"] = 1024
        else:
            params["max_tokens"] = 1024
            params["temperature"] = 0.0 
        
        return await self.client.chat.completions.create(**params)

    def _extract_field_value(self, response, structured_spec: Dict) -> str:
        """
        Extract field value from structured response.

        For object schemas (with explanation), returns the full JSON string.
        For enum schemas, returns just the field value.

        Args:
            response: OpenAI response object (from chat.completions.create)
            structured_spec: Schema specification with field name

        Returns:
            Extracted value as string (either single value or full JSON)
        """
        try:
            text_out = response.choices[0].message.content
        except (AttributeError, IndexError):
            logger.warning("Failed to extract content from OpenAI structured response")
            return ""

        if not text_out:
            return ""

        schema_type = structured_spec.get('type')

        # For object schemas, return the full JSON (includes explanation)
        if schema_type == 'object':
            return text_out

        # For legacy enum schemas, extract just the field value
        try:
            data = json.loads(text_out)
            field_name = structured_spec.get('field', 'route')
            value = data.get(field_name)
            return str(value) if value is not None else text_out
        except Exception:
            return text_out

    async def generate_image(
        self,
        prompt: str,
        model: str = "dall-e-3",
        size: str = "1024x1024",
        quality: str = "standard",
        style: str = "vivid"
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Generate an image using DALL-E and stream the result like chat completions.

        This follows the same pattern as stream_chat_completion to integrate cleanly
        with the existing message flow.

        Args:
            prompt: Text description of the image to generate
            model: DALL-E model ("dall-e-3" or "dall-e-2")
            size: Image size
            quality: Image quality
            style: Image style

        Yields:
            Tuple of (status_message, metadata_dict)
        """
        try:
            # Yield initial status
            yield "Generating image...", None

            # Build request parameters
            request_params = {
                "model": model,
                "prompt": prompt,
                "size": size,
                "response_format": "b64_json",
                "n": 1,
            }

            # Add quality and style for DALL-E 3
            if model == "dall-e-3":
                request_params["quality"] = quality
                request_params["style"] = style

            # Call OpenAI Images API
            response = await self.client.images.generate(**request_params)

            # Extract response data
            image_data = response.data[0]
            revised_prompt = getattr(image_data, 'revised_prompt', prompt)
            image_bytes = base64.b64decode(image_data.b64_json)

            # Calculate cost
            cost = self._calculate_dalle_cost(model, size, quality)

            # Yield final result with metadata
            metadata = {
                "image_bytes": image_bytes,
                "revised_prompt": revised_prompt,
                "cost": cost,
                "model": model,
                "size": size,
                "quality": quality,
                "style": style,
                "input_tokens": 0,  # Images don't use tokens
                "output_tokens": 0,
            }

            yield "Image generated successfully", metadata

        except Exception as e:
            logger.exception("DALL-E generation error")
            error_message = OpenAIErrorHandler.format_error(e)
            yield f"Error: {error_message}", None

    def _calculate_dalle_cost(self, model: str, size: str, quality: str) -> Decimal:
        """Calculate DALL-E generation cost."""
        pricing = {
            "dall-e-3": {
                "1024x1024": {"standard": Decimal("0.040"), "hd": Decimal("0.080")},
                "1024x1792": {"standard": Decimal("0.080"), "hd": Decimal("0.120")},
                "1792x1024": {"standard": Decimal("0.080"), "hd": Decimal("0.120")},
            },
            "dall-e-2": {
                "1024x1024": {"standard": Decimal("0.020")},
                "512x512": {"standard": Decimal("0.018")},
                "256x256": {"standard": Decimal("0.016")},
            },
        }

        try:
            model_pricing = pricing.get(model, pricing["dall-e-3"])
            size_pricing = model_pricing.get(size, model_pricing["1024x1024"])

            if isinstance(size_pricing, dict):
                return size_pricing.get(quality, size_pricing.get("standard", Decimal("0.040")))
            return size_pricing
        except Exception:
            return Decimal("0.040")

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
