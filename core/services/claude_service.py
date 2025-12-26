"""
Claude LLM service implementation.

This service provides a clean, readable interface for interacting with Anthropic's
Claude models, including support for streaming, vision, web search, and structured outputs.
"""

import json
import logging
from typing import AsyncGenerator, Dict, List, Tuple, Optional

from anthropic import AsyncAnthropic

from config import env
from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key
from core.services.llm_utils import (
    MessageFormatter,
    ClaudeVisionHandler,
    ClaudeErrorHandler,
    ClaudeStreamProcessor,
    ClaudeWebSearchTools,
    StreamAggregator,
    SchemaTransformer,
)

logger = logging.getLogger(__name__)


class ClaudeService:
    """Service for interacting with Anthropic's Claude models."""

    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize Claude service.

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
    def client(self) -> AsyncAnthropic:
        """
        Lazy initialization of Claude client.

        This prevents issues with async HTTP clients in RQ background workers
        by creating the client on first use rather than during __init__.
        """
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key)
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
        Stream chat completions from Claude API.

        This is the main public method for streaming responses. It orchestrates
        the entire streaming process with clear separation of concerns.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            images: List of image dicts for vision support
            tools: Optional tools for web search support

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        try:
            # Step 1: Prepare messages with vision if needed
            prepared_messages = self._prepare_messages(messages, images)

            # Step 2: Create streaming response
            stream = await self._create_stream(
                prepared_messages,
                max_tokens,
                temperature,
                tools
            )

            # Step 3: Process and yield stream chunks
            async for chunk, usage in ClaudeStreamProcessor.process_stream(stream):
                yield chunk, usage

        except Exception as e:
            logger.exception(f"Error streaming chat completion: {str(e)}")
            error_message = ClaudeErrorHandler.format_error(e)
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

    async def generate_structured_output(
        self,
        messages: List[Dict[str, str]],
        response_schema: Dict,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> Dict:
        """
        Generate response matching a JSON schema using Claude's native structured outputs.

        Uses Claude's official structured outputs API (beta) for guaranteed JSON compliance.
        Structured outputs require Claude 4.x models (Sonnet 4.5, Opus 4.1/4.5, Haiku 4.5).
        If current model doesn't support it, falls back to claude-sonnet-4-5-20250514.

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
        logger.info(f"[Claude] generate_structured_output with schema: {list(response_schema.get('properties', {}).keys())}")

        # Models that support structured output (Claude 4.x only)
        SUPPORTED_MODELS = [
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-1-20250929",
            "claude-opus-4-5-20250929",
            "claude-haiku-4-5-20250929",
        ]
        
        # Use the configured model if it supports structured output, otherwise use Sonnet 4.5
        model_to_use = self.model
        if not any(supported in self.model for supported in SUPPORTED_MODELS):
            model_to_use = "claude-sonnet-4-5-20250929"
            logger.info(f"[Claude] Model {self.model} doesn't support structured output, using {model_to_use}")

        # Extract system message (Claude requires it separately)
        system_message, filtered_messages = MessageFormatter.extract_system_messages(messages)

        params = {
            "model": model_to_use,
            "max_tokens": max_tokens,
            "messages": filtered_messages,
            "temperature": temperature,
            "betas": ["structured-outputs-2025-11-13"],
            "output_format": {
                "type": "json_schema",
                "schema": response_schema,
            }
        }

        if system_message:
            params["system"] = system_message

        try:
            # Use beta client for structured outputs
            response = await self.client.beta.messages.create(**params)

            # Check for refusal
            if response.stop_reason == "refusal":
                raise ValueError("Claude refused to generate structured output")

            # Parse JSON from response content
            if response.content and len(response.content) > 0:
                content = response.content[0].text
                return json.loads(content)

            raise ValueError("Empty response from Claude structured output")

        except Exception as e:
            logger.exception(f"[Claude] generate_structured_output error: {str(e)}")
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

        return ClaudeVisionHandler.add_images_to_messages(messages, images)

    async def _create_stream(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]]
    ):
        """
        Create Claude streaming response.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools configuration

        Returns:
            Claude message stream
        """
        call_params = self._build_stream_params(
            messages,
            max_tokens,
            temperature,
            tools
        )

        return await self.client.messages.create(**call_params)

    def _build_stream_params(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        tools: Optional[List[Dict]]
    ) -> Dict:
        """
        Build parameters for Claude stream API call.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            tools: Optional tools configuration

        Returns:
            API call parameters dictionary
        """
        # Extract system message (Claude requires it separately)
        system_message, filtered_messages = MessageFormatter.extract_system_messages(messages)

        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": filtered_messages,
            "temperature": temperature,
            "stream": True
        }

        # Add system message if present
        if system_message:
            params["system"] = system_message

        # Add tools if provided (convert from OpenAI format to Claude format)
        if tools:
            claude_tools = self._convert_tools_to_claude_format(tools)
            params["tools"] = claude_tools
            logger.info(f"[Claude] Converted {len(tools)} tools to Claude format: {[t.get('name') for t in claude_tools]}")
            # Force tool use when tools are provided - ensures LLM uses tools instead of text
            if claude_tools:
                params["tool_choice"] = {"type": "any"}
                logger.info("[Claude] Set tool_choice='any' to force tool usage")

        return params

    def _convert_tools_to_claude_format(self, tools: List[Dict]) -> List[Dict]:
        """
        Convert OpenAI-style tool definitions to Claude format.

        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}
            }
        }

        Claude format:
        {
            "name": "...",
            "description": "...",
            "input_schema": {...}
        }

        Args:
            tools: List of tools in OpenAI format

        Returns:
            List of tools in Claude format
        """
        claude_tools = []
        for tool in tools:
            # Check if it's already in Claude format
            if "name" in tool and "input_schema" in tool:
                claude_tools.append(tool)
                continue

            # Convert from OpenAI format
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                claude_tool = {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                }
                claude_tools.append(claude_tool)
            else:
                # Unknown format, try to pass through
                logger.warning(f"Unknown tool format, passing through: {tool}")
                claude_tools.append(tool)

        return claude_tools

    async def _get_structured_completion(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        structured_spec: Dict
    ) -> str:
        """
        Get structured output using prompt engineering.

        Claude doesn't have native structured outputs, so we use prompt engineering.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            structured_spec: Schema specification

        Returns:
            Generated response with instructions appended
        """
        instruction = SchemaTransformer.transform_for_claude(structured_spec)

        if instruction:
            messages = self._append_instruction_to_messages(messages, instruction)

        # Use streaming and aggregate
        stream = self.stream_chat_completion(messages, max_tokens, temperature)
        return await StreamAggregator.aggregate_stream(stream)

    def _append_instruction_to_messages(
        self,
        messages: List[Dict],
        instruction: str
    ) -> List[Dict]:
        """
        Append instruction to the last user/assistant message.

        Args:
            messages: List of messages
            instruction: Instruction to append

        Returns:
            Modified messages list
        """
        # Find last user/assistant message and append instruction
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get('role') in ['user', 'assistant']:
                # Copy to avoid mutating original
                messages[i] = messages[i].copy()
                messages[i]['content'] = messages[i].get('content', '') + instruction
                break

        return messages

    # ==================== Static Methods ====================

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Get the native web search tool definition for Claude API.

        Returns:
            Web search tool dictionary
        """
        return ClaudeWebSearchTools.get_tool_definition()
