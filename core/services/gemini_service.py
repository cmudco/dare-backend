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
from core.services.api_key_service import get_provider_api_key
from core.services.llm_utils import (
    GeminiMessageFormatter,
    GeminiVisionHandler,
    GeminiErrorHandler,
    GeminiStreamProcessor,
    GeminiUrlContextTools,
    GeminiWebSearchTools,
    StreamAggregator,
    SchemaTransformer,
)
from core.services.model_capabilities import ModelCapabilities

logger = logging.getLogger(__name__)


class GeminiService:
    """Service for interacting with Google Gemini models."""

    # IMPORTANT: Add 3000 tokens buffer to all API calls.
    # This is required due to a bug in the Gemini package.
    TOKEN_BUFFER = 3000

    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize Gemini service.

        Args:
            llm: LLM model instance with configuration
            api_key: Optional API key override. If not provided, uses provider key resolution
        """
        # Use provided key or fetch from provider key service
        if api_key is None:
            api_key = get_provider_api_key(llm.provider)

        self.api_key = api_key
        self._client = None
        self.model_identifier = llm.identifier
        self.is_reasoning = llm.is_reasoning
        self.capabilities = ModelCapabilities.from_llm(llm)

    @property
    def client(self) -> genai.Client:
        """
        Lazy initialization of Gemini client.

        This prevents issues with HTTP clients in RQ background workers
        by creating the client on first use rather than during __init__.
        """
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    @property
    def async_client(self):
        """
        Get the native async interface for the Gemini client.
        
        Uses client.aio for true real-time streaming without blocking.
        """
        return self.client.aio

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        effort: Optional[str] = None,
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
        effort: Optional[str] = None,
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
        stream = self.stream_chat_completion(messages, max_tokens, temperature, effort)
        return await StreamAggregator.aggregate_stream(stream)

    async def generate_structured_output(
        self,
        messages: List[Dict[str, str]],
        response_schema: Dict,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> Dict:
        """
        Generate response matching a JSON schema using Gemini's native structured outputs.

        Uses Gemini's response_schema for guaranteed JSON compliance.

        Args:
            messages: List of message dictionaries
            response_schema: JSON Schema the response must match
            max_tokens: Maximum tokens to generate
            temperature: Controls randomness

        Returns:
            Parsed JSON response as dictionary

        Raises:
            ValueError: If schema validation fails or no response returned
        """
        logger.info(f"[Gemini] generate_structured_output with schema: {list(response_schema.get('properties', {}).keys())}")

        contents = GeminiMessageFormatter.convert_to_contents(messages)

        # IMPORTANT: Add 3000 tokens buffer due to Gemini package bug
        generation_config_kwargs = {
            "max_output_tokens": max_tokens + self.TOKEN_BUFFER,
            "response_mime_type": "application/json",
            "response_schema": response_schema,
        }
        if self.capabilities.supports_temperature:
            generation_config_kwargs["temperature"] = temperature
        generation_config = types.GenerateContentConfig(**generation_config_kwargs)

        def generate_sync():
            return self.client.models.generate_content(
                model=self.model_identifier,
                contents=contents,
                config=generation_config,
            )

        try:
            response = await asyncio.to_thread(generate_sync)
            content = getattr(response, 'text', None)

            if not content:
                raise ValueError("Empty response from Gemini structured output")

            return json.loads(content)

        except Exception as e:
            logger.exception(f"[Gemini] generate_structured_output error: {str(e)}")
            raise ValueError(f"Structured output generation failed: {str(e)}")

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
        Create Gemini streaming response using native async interface.

        Uses client.aio for true real-time streaming - chunks are delivered
        as they arrive from the API, not buffered.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools configuration

        Returns:
            Async Gemini response stream
        """
        contents = GeminiMessageFormatter.convert_to_contents(messages)
        config = self._build_generation_config(max_tokens, temperature, tools)

        # Use native async interface for true real-time streaming
        return await self.async_client.models.generate_content_stream(
            model=self.model_identifier,
            contents=contents,
            config=config,
        )

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
        # IMPORTANT: Add 3000 tokens buffer due to Gemini package bug
        config_kwargs = {"max_output_tokens": max_tokens + self.TOKEN_BUFFER}
        if self.capabilities.supports_temperature:
            config_kwargs["temperature"] = temperature
        config = types.GenerateContentConfig(**config_kwargs)

        native_tools = self._build_native_tools(tools)
        if native_tools:
            config.tools = native_tools
            if self._has_function_tools(tools):
                logger.warning(
                    "[Gemini] Native tools cannot be combined with function "
                    "calling; using native Gemini tools only"
                )
        elif tools:
            # Handle tools - could be native Gemini types.Tool or OpenAI format dicts
            gemini_tools = []
            
            for tool in tools:
                # Check if it's already a Gemini types.Tool object
                if isinstance(tool, types.Tool):
                    gemini_tools.append(tool)
                # OpenAI format: {"type": "function", "function": {...}}
                elif isinstance(tool, dict) and tool.get("type") == "function":
                    func = tool.get("function", {})
                    func_decl = types.FunctionDeclaration(
                        name=func.get("name", ""),
                        description=func.get("description", ""),
                        parameters=func.get("parameters", {})
                    )
                    gemini_tools.append(types.Tool(function_declarations=[func_decl]))
            
            if gemini_tools:
                config.tools = gemini_tools
                # Force function calling when tools are provided
                # This ensures Gemini uses the tool instead of generating raw text
                config.tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY"  # Force the model to use a function
                    )
                )
                logger.debug(f"[Gemini] Set {len(gemini_tools)} tools with tool_config mode=ANY")

        return config

    def _convert_tools_to_gemini_format(self, tools: List[Dict]) -> List:
        """
        Convert OpenAI-style tool definitions to Gemini format.

        Args:
            tools: List of tools in OpenAI format

        Returns:
            List of Gemini Tool objects
        """
        function_declarations = []

        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                # Build Gemini function declaration
                func_decl = types.FunctionDeclaration(
                    name=func.get("name", ""),
                    description=func.get("description", ""),
                    parameters=func.get("parameters", {})
                )
                function_declarations.append(func_decl)

        if function_declarations:
            return [types.Tool(function_declarations=function_declarations)]
        return []

    def _build_native_tools(self, tools: Optional[List[Dict]]) -> List[types.Tool]:
        """
        Build Gemini-native tool objects from provider tool indicators.

        URL Context and Google Search are provider-native tools. They are not
        converted into function declarations, so they must bypass the generic
        function-calling branch.
        """
        if not tools:
            return []

        native_tools = []
        if GeminiWebSearchTools.has_google_search(tools):
            native_tools.append(GeminiWebSearchTools.build_google_search_tool())
        if GeminiUrlContextTools.has_url_context(tools):
            native_tools.append(GeminiUrlContextTools.build_url_context_tool())

        return native_tools

    @staticmethod
    def _has_function_tools(tools: Optional[List[Dict]]) -> bool:
        """Return whether an OpenAI-format function tool is present."""
        if not tools:
            return False
        return any(
            isinstance(tool, dict) and tool.get("type") == "function"
            for tool in tools
        )

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
        logger.info(
            f"[Gemini] _get_structured_completion called with model: {self.model_identifier}, "
            f"spec: {structured_spec}"
        )

        response_mime_type, response_schema = SchemaTransformer.transform_for_gemini(
            structured_spec
        )
        logger.info(
            f"[Gemini] Transformed schema - mime_type: {response_mime_type}, "
            f"schema: {response_schema}"
        )

        if not response_mime_type or not response_schema:
            # Fallback to regular completion
            logger.warning("[Gemini] No response schema generated, falling back to streaming")
            stream = self.stream_chat_completion(messages, max_tokens, temperature)
            return await StreamAggregator.aggregate_stream(stream)

        # Generate with schema
        logger.info("[Gemini] Calling native structured output API with response_schema")
        response = await self._generate_with_schema(
            messages,
            max_tokens,
            temperature,
            response_mime_type,
            response_schema
        )
        logger.info(f"[Gemini] Native API response received: {str(response)[:200]}...")

        # Extract and return field value
        extracted_value = self._extract_field_value(response, structured_spec)
        logger.info(f"[Gemini] Extracted field value: {extracted_value}")
        return extracted_value

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

        # IMPORTANT: Add 3000 tokens buffer due to Gemini package bug
        generation_config_kwargs = {
            "max_output_tokens": max_tokens + self.TOKEN_BUFFER,
            "response_mime_type": response_mime_type,
            "response_schema": response_schema,
        }
        if self.capabilities.supports_temperature:
            generation_config_kwargs["temperature"] = temperature
        generation_config = types.GenerateContentConfig(**generation_config_kwargs)

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

        For object schemas (with explanation), returns the full JSON string.
        For enum schemas, returns just the field value.

        Args:
            response: Gemini response object
            structured_spec: Schema specification with field name

        Returns:
            Extracted value as string (either single value or full JSON)
        """
        text_out = getattr(response, 'text', None)

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

    # ==================== Static Methods ====================

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Get the native Google Search tool definition for Gemini API.

        Returns:
            Web search tool dictionary
        """
        return GeminiWebSearchTools.get_tool_definition()

    @staticmethod
    def get_web_fetch_tool() -> Dict:
        """
        Get the native URL Context tool definition for Gemini API.

        DARE exposes this behind the same Web Fetch toggle as Claude because
        the user-facing intent is identical: fetch explicit URLs/PDFs supplied
        in the prompt.
        """
        return GeminiUrlContextTools.get_tool_definition()
