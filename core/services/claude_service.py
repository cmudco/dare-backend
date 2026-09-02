"""
Claude LLM service implementation.

This service provides a clean, readable interface for interacting with Anthropic's
Claude models, including support for streaming, vision, web search, and structured outputs.
"""

import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic
from django.core.exceptions import SynchronousOnlyOperation

from config import env
from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key_sync
from core.services.dtos.stream_event_dto import LLMStreamEvent
from core.services.llm_utils import (
    ClaudeErrorHandler,
    ClaudeStreamProcessor,
    ClaudeVisionHandler,
    ClaudeWebFetchTools,
    ClaudeWebSearchTools,
    MessageFormatter,
    SchemaTransformer,
    StreamAggregator,
)
from core.services.llm_utils.provider_message_converters import ClaudeMessageConverter
from core.services.model_capabilities import ModelCapabilities

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
            try:
                api_key = get_provider_api_key_sync(llm.provider)
            except SynchronousOnlyOperation:
                api_key = getattr(env, "CLAUDE_API_KEY", None)

        self.api_key = api_key
        self._client = None
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning
        self.capabilities = ModelCapabilities.from_llm(llm)

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
        effort: Optional[str] = None,
        images: List[Dict] = None,
        tools: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
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
            LLMStreamEvent
        """
        try:
            # Step 1: Prepare messages with vision if needed
            prepared_messages = self._prepare_messages(messages, images)

            # Step 2: Create streaming response
            stream = await self._create_stream(
                prepared_messages, max_tokens, temperature, effort, tools
            )

            # Step 3: Process and yield stream events
            resolved_effort = self.capabilities.resolve_effort(effort)
            async for event in ClaudeStreamProcessor.process_stream(stream):
                if event.usage is not None:
                    usage = dict(event.usage)
                    usage["request_max_tokens"] = max_tokens
                    if resolved_effort:
                        usage["effort"] = resolved_effort
                    yield LLMStreamEvent.usage_frame(usage)
                else:
                    yield event

        except Exception as e:
            logger.exception(f"Error streaming chat completion: {str(e)}")
            error_message = ClaudeErrorHandler.format_error(e)
            yield LLMStreamEvent.text_delta(f"Error: {error_message}")

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
                messages, max_tokens, temperature, effort, structured_spec
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
        effort: Optional[str] = None,
    ) -> Dict:
        """Generate a response matching a JSON schema via Claude's structured outputs."""
        result, _ = await self.generate_structured_output_with_usage(
            messages, response_schema, max_tokens, temperature, effort
        )
        return result

    async def generate_structured_output_with_usage(
        self,
        messages: List[Dict[str, str]],
        response_schema: Dict,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        effort: Optional[str] = None,
    ) -> Tuple[Dict, Dict[str, int]]:
        """Structured output plus normalized token usage for service billing.

        Structured outputs need a Claude 4.x or newer model; a Claude 3 model
        is swapped for Sonnet 4.5 for this call only.
        """
        logger.info(
            f"[Claude] generate_structured_output with schema: {list(response_schema.get('properties', {}).keys())}"
        )

        model_to_use = self.model
        if "claude-3" in self.model:
            model_to_use = "claude-sonnet-4-5-20250929"
            logger.info(
                f"[Claude] Model {self.model} doesn't support structured output, using {model_to_use}"
            )

        # Extract system message (Claude requires it separately)
        system_message, filtered_messages = MessageFormatter.extract_system_messages(
            messages
        )

        params = {
            "model": model_to_use,
            "max_tokens": max_tokens,
            "messages": ClaudeVisionHandler.convert_image_parts(filtered_messages),
            "betas": ["structured-outputs-2025-11-13"],
            "output_format": {
                "type": "json_schema",
                "schema": response_schema,
            },
        }
        self._apply_claude_sampling(params, temperature, effort)
        self._move_output_config_to_extra_body(params)

        if system_message:
            params["system"] = system_message

        try:
            # Use beta client for structured outputs
            response = await self.client.beta.messages.create(**params)

            # Check for refusal
            if response.stop_reason == "refusal":
                raise ValueError("Claude refused to generate structured output")

            if not response.content:
                raise ValueError("Empty response from Claude structured output")

            usage = getattr(response, "usage", None)
            return json.loads(response.content[0].text), {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            }

        except Exception as e:
            logger.exception(f"[Claude] generate_structured_output error: {str(e)}")
            raise ValueError(f"Structured output generation failed: {str(e)}")

    # ==================== Private Methods ====================

    def _prepare_messages(
        self, messages: List[Dict], images: Optional[List[Dict]]
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
        effort: Optional[str],
        tools: Optional[List[Dict]],
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
            messages, max_tokens, temperature, effort, tools
        )

        return await self.client.messages.create(**call_params)

    def _build_stream_params(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        effort: Optional[str],
        tools: Optional[List[Dict]],
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
        # Convert internal (OpenAI-format) messages: extracts the system
        # prompt and translates tool_calls / role:"tool" turns into Claude's
        # tool_use / tool_result content blocks.
        system_message, converted_messages = ClaudeMessageConverter.convert(messages)

        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": converted_messages,
            "stream": True,
        }
        self._apply_claude_sampling(params, temperature, effort)
        self._move_output_config_to_extra_body(params)

        # Add system message if present
        if system_message:
            params["system"] = [
                {
                    "type": "text",
                    "text": system_message,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        # A breakpoint on the latest turn caches the whole conversation
        # prefix, so the next turn only pays full price for what is new.
        self._mark_last_turn_cacheable(converted_messages)

        # Add tools if provided (convert from OpenAI format to Claude format)
        if tools:
            claude_tools = self._convert_tools_to_claude_format(tools)
            params["tools"] = claude_tools
            if ClaudeWebFetchTools.has_web_fetch(claude_tools):
                params["extra_headers"] = {
                    "anthropic-beta": ClaudeWebFetchTools.BETA_HEADER
                }
            logger.debug(
                f"[Claude] Converted {len(tools)} tools to Claude format: {[t.get('name') for t in claude_tools]}"
            )
            # Let LLM decide when to use tools (auto is default, so no need to set explicitly)

        return params

    @staticmethod
    def _mark_last_turn_cacheable(messages: List[Dict]) -> None:
        if not messages:
            return
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, str):
            if not content:
                return
            last["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            block = content[-1]
            if isinstance(block, dict) and block.get("type") in (
                "text",
                "tool_result",
                "image",
                "document",
            ):
                block["cache_control"] = {"type": "ephemeral"}

    def _apply_claude_sampling(
        self,
        params: Dict,
        temperature: float,
        effort: Optional[str] = None,
    ) -> None:
        """Apply generation controls in the Anthropic dialect.

        ``output_config`` and ``thinking`` are Anthropic shapes, so they are
        written here rather than by a shared helper — every other transport
        rejects them as unknown arguments.
        """
        if self.capabilities.supports_temperature:
            params["temperature"] = temperature

        resolved_effort = self.capabilities.resolve_effort(effort)
        if resolved_effort:
            params["output_config"] = {"effort": resolved_effort}

        if (
            self.capabilities.supports_adaptive_thinking
            and self.capabilities.default_adaptive_thinking_enabled
        ):
            params["thinking"] = {"type": "adaptive", "display": "summarized"}

    @staticmethod
    def _move_output_config_to_extra_body(params: Dict) -> None:
        """
        Send newer Anthropic fields before the installed SDK exposes typed args.

        The local anthropic SDK accepts ``thinking`` directly, but not
        ``output_config`` yet. Passing it through ``extra_body`` preserves the
        wire payload without tripping client-side argument validation.
        """
        output_config = params.pop("output_config", None)
        if not output_config:
            return

        extra_body = params.setdefault("extra_body", {})
        extra_body["output_config"] = output_config

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
                    "input_schema": func.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
                claude_tools.append(claude_tool)
            elif "type" in tool and "name" in tool:
                claude_tools.append(tool)
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
        effort: Optional[str],
        structured_spec: Dict,
    ) -> str:
        """
        Get structured output using Claude's native structured output API.

        Claude now supports native structured outputs via the beta API
        (structured-outputs-2025-11-13). This method transforms the unified
        spec to Claude's format and returns JSON.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            structured_spec: Unified schema specification

        Returns:
            JSON string response from the model
        """
        # Transform unified spec to Claude's JSON schema format
        response_schema = SchemaTransformer.transform_for_claude(structured_spec)

        if not response_schema:
            logger.warning(
                "[Claude] Could not transform spec to schema, falling back to streaming"
            )
            stream = self.stream_chat_completion(
                messages, max_tokens, temperature, effort
            )
            return await StreamAggregator.aggregate_stream(stream)

        # Use native structured output API
        logger.info(
            f"[Claude] Using native structured output with schema: {list(response_schema.get('properties', {}).keys())}"
        )
        result = await self.generate_structured_output(
            messages=messages,
            response_schema=response_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
        )

        # Return as JSON string (consistent with other providers)
        return json.dumps(result)

    # ==================== Static Methods ====================

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Get the native web search tool definition for Claude API.

        Returns:
            Web search tool dictionary
        """
        return ClaudeWebSearchTools.get_tool_definition()

    @staticmethod
    def get_web_fetch_tool() -> Dict:
        """
        Get the native web fetch tool definition for Claude API.

        Returns:
            Web fetch tool dictionary
        """
        return ClaudeWebFetchTools.get_tool_definition()
