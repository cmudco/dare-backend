"""
OpenAI LLM service implementation.

This service provides a clean, readable interface for interacting with OpenAI's
GPT models, including support for streaming, vision, web search, and structured outputs.
"""

import base64
import json
import logging
from decimal import Decimal
from typing import AsyncGenerator, Dict, List, Optional, Sequence, Tuple, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from conversations.models import LLM
from core.services.api_key_service import get_provider_api_key
from core.services.code_sandbox import (
    OPENAI_CODE_INTERPRETER,
    SANDBOX_TOOL_TYPE,
    OpenAISandboxFiles,
)
from core.services.dtos.stream_event_dto import LLMStreamEvent
from core.services.image_formats import to_provider_image
from core.services.llm_utils import (
    OpenAIErrorHandler,
    OpenAIMessageFormatter,
    OpenAIStreamProcessor,
    OpenAIUsageExtractor,
    OpenAIVisionHandler,
    OpenAIWebSearchTools,
    SchemaTransformer,
    StreamAggregator,
)
from core.services.model_capabilities import ModelCapabilities
from core.services.structured_output_error import (
    StructuredOutputError,
    StructuredOutputFailure,
)

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "gpt-image-2.6-sunburst"

# GPT Image 2.6 prices in USD per 1M tokens (Sunburst and Flare share them):
# https://developers.openai.com/api/docs/pricing
GPT_IMAGE_TEXT_INPUT_RATE = Decimal("5.00")
GPT_IMAGE_IMAGE_INPUT_RATE = Decimal("8.00")
GPT_IMAGE_OUTPUT_RATE = Decimal("30.00")

GPT_IMAGE_SIZES = frozenset({"auto", "1024x1024", "1536x1024", "1024x1536"})
GPT_IMAGE_QUALITIES = frozenset({"auto", "low", "medium", "high"})
# The edit endpoint takes fewer formats than chat vision does (no GIF).
GPT_IMAGE_EDIT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class OpenAIService:
    """Service for interacting with OpenAI's GPT models."""

    def __init__(
        self,
        llm: LLM,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize OpenAI service.

        Args:
            llm: LLM model instance with configuration
            api_key: Optional API key override. If not provided, uses provider key resolution
            base_url: Optional override for the API endpoint. Set when routing
                through a LiteLLM proxy (or any other OpenAI-compatible
                gateway); leave ``None`` for direct calls to api.openai.com.
        """
        if api_key is None:
            api_key = get_provider_api_key(llm.provider)

        self.api_key = api_key
        self.base_url = base_url
        self._client = None
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning
        self.capabilities = ModelCapabilities.from_llm(llm)

    @property
    def client(self) -> AsyncOpenAI:
        """
        Lazy initialization of OpenAI client.

        This prevents issues with async HTTP clients in RQ background workers
        by creating the client on first use rather than during __init__.
        """
        if self._client is None:
            client_kwargs = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    def _uses_max_completion_tokens(self) -> bool:
        """
        Determine whether this model expects max_completion_tokens instead of max_tokens.

        OpenAI reasoning families reject max_tokens on Chat Completions.
        """
        if self.is_reasoning:
            return True
        return self.model.startswith(("gpt-5", "o1", "o3", "o4"))

    def _token_limit(self, max_tokens: int) -> Dict[str, int]:
        """The Chat Completions output cap under the name this model accepts."""
        if self._uses_max_completion_tokens():
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens}

    def _uses_responses_api(self, tools: Optional[List[Dict]]) -> bool:
        """
        Determine whether this request should go through the Responses API.

        Anything tool-shaped belongs there: it is the only OpenAI surface that
        accepts function tools on reasoning models (the whole GPT-5.x family)
        and the only one that can run web search alongside them.

        Reasoning models go there too, with or without tools: it is the only
        surface that streams their thinking summaries, and Chat Completions
        stays silent for as long as they think.

        The exception is a custom ``base_url``, which means we are talking to a
        LiteLLM proxy or another OpenAI-compatible gateway rather than OpenAI
        itself. Those speak /v1/chat/completions but not necessarily
        /v1/responses, so proxied traffic keeps the Chat Completions path.

        Args:
            tools: Tools requested for this turn, if any

        Returns:
            True when the Responses API should be used
        """
        return (bool(tools) or self.is_reasoning) and self.base_url is None

    async def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        effort: Optional[str] = None,
        images: Optional[List[Dict]] = None,
        tools: Optional[List[Dict]] = None,
        allow_tool_calls: bool = True,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """
        Stream one model turn as provider-agnostic events.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            max_tokens: Maximum number of tokens to generate
            temperature: Controls randomness (0.0 to 1.0)
            effort: Reasoning effort, sent when the model supports it
            images: List of image dicts for vision support
            tools: Function tools and the web search marker for this turn
            allow_tool_calls: False on a tool loop's forced final round; the
                tools stay in the request but the model may not call them

        Yields:
            LLMStreamEvent
        """
        try:
            prepared_messages = self._prepare_messages(messages, images)

            if self._uses_responses_api(tools):
                response = await self._stream_responses_api(
                    prepared_messages,
                    max_tokens,
                    temperature,
                    effort,
                    tools,
                    allow_tool_calls,
                )
                processor = OpenAIStreamProcessor.process_responses_api_stream
            else:
                response = await self._stream_chat_completions(
                    prepared_messages,
                    max_tokens,
                    temperature,
                    effort,
                    tools,
                    allow_tool_calls,
                )
                processor = OpenAIStreamProcessor.process_chat_completion_stream

            async for event in processor(response):
                if event.usage and event.usage.get("sandbox_citations"):
                    await self._collect_sandbox_outputs(event.usage)
                yield event

        except Exception as e:
            logger.exception("OpenAI streaming error")
            error_message = OpenAIErrorHandler.format_error(e)
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
            return await self._get_structured_completion(messages, structured_spec)

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
        """
        Generate response matching a JSON schema using native structured outputs.

        This method provides reliable JSON responses for artifact planning.
        Uses OpenAI's json_schema response format for guaranteed compliance.

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
        result, _usage = await self.generate_structured_output_with_usage(
            messages=messages,
            response_schema=response_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
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
        """Structured output plus normalized token usage for service billing."""
        logger.info(
            f"[OpenAI] generate_structured_output with schema: {list(response_schema.get('properties', {}).keys())}"
        )

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "schema": response_schema,
                "strict": True,
            },
        }

        params = {
            "model": self.model,
            "messages": messages,
            "response_format": response_format,
            **self._token_limit(max_tokens),
        }

        self._apply_openai_sampling(params, temperature, effort)

        response = await self.client.chat.completions.create(**params)
        provider_usage = getattr(response, "usage", None)
        usage = {
            "input_tokens": int(getattr(provider_usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(provider_usage, "completion_tokens", 0) or 0),
        }
        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise StructuredOutputError(StructuredOutputFailure.EMPTY, usage)
        if getattr(choice.message, "refusal", None):
            raise StructuredOutputError(StructuredOutputFailure.REFUSAL, usage)
        if choice.finish_reason == "content_filter":
            raise StructuredOutputError(StructuredOutputFailure.FILTERED, usage)
        if choice.finish_reason == "length":
            raise StructuredOutputError(StructuredOutputFailure.LENGTH, usage)
        content = choice.message.content
        if not content:
            raise StructuredOutputError(StructuredOutputFailure.EMPTY, usage)
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Never attach provider content (which can contain document text) to logs.
            raise StructuredOutputError(
                StructuredOutputFailure.INVALID_JSON, usage
            ) from None
        return result, usage

    async def parse_structured_output(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[StructuredModel],
        max_tokens: int = 2000,
    ) -> Tuple[StructuredModel, Dict[str, int]]:
        """Return a validated Pydantic response and its token usage."""
        params = {
            "model": self.model,
            "messages": messages,
            "response_format": response_model,
            **self._token_limit(max_tokens),
        }

        self._apply_openai_sampling(params, 0.0)
        response = await self.client.chat.completions.parse(**params)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError("Empty structured response from OpenAI")

        usage = OpenAIUsageExtractor.extract_from_chat_completion(response) or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        return parsed, usage

    async def close(self) -> None:
        """Close this service's lazily-created client."""
        if self._client is None:
            return
        try:
            await self._client.close()
        except Exception:
            logger.exception("[OpenAI] Failed to close async client")
        finally:
            self._client = None

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

        return OpenAIVisionHandler.add_images_to_messages(messages, images)

    async def _stream_responses_api(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        effort: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        allow_tool_calls: bool = True,
    ):
        """
        Stream using the Responses API.

        This is the path for reasoning models and for anything tool-shaped:
        web search, function calling, or both together. Chat Completions
        rejects function tools on reasoning models (the whole GPT-5.x family)
        unless reasoning is disabled, and it cannot mix web search with
        function tools at all.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            effort: Reasoning effort, sent when the model supports it
            tools: Tools in Chat Completions shape (converted here)

        Returns:
            OpenAI Responses API stream
        """
        params = {
            "model": self.model,
            "input": OpenAIMessageFormatter.format_for_responses_api(messages),
            "max_output_tokens": max_tokens,
            "stream": True,
            # The Responses API keeps every response for 30 days unless told
            # not to; Chat Completions never did.
            "store": False,
        }

        native_tools = [await self._native_tool(tool) for tool in tools or []]
        if native_tools:
            params["tools"] = OpenAIMessageFormatter.format_tools_for_responses_api(
                native_tools
            )
            if not allow_tool_calls:
                params["tool_choice"] = "none"
        if any(tool.get("type") == OPENAI_CODE_INTERPRETER for tool in native_tools):
            params["include"] = ["code_interpreter_call.outputs"]

        if self.is_reasoning:
            # OpenAI decides per reply whether to send a summary; "detailed"
            # gets one more often than "auto".
            params["reasoning"] = {"summary": "detailed"}
            resolved_effort = self.capabilities.resolve_effort(effort)
            if resolved_effort:
                params["reasoning"]["effort"] = resolved_effort

        if self.capabilities.supports_temperature:
            params["temperature"] = temperature

        return await self.client.responses.create(**params)

    async def _native_tool(self, tool: Dict) -> Dict:
        """Turn DARE's sandbox marker into a container holding the files."""
        if tool.get("type") != SANDBOX_TOOL_TYPE:
            return tool
        file_ids = await OpenAISandboxFiles(
            self.client, self.api_key
        ).provider_file_ids(tool["dare_file_ids"], tool["dare_owner_ids"])
        return {
            "type": OPENAI_CODE_INTERPRETER,
            "container": {"type": "auto", "file_ids": file_ids},
        }

    async def _collect_sandbox_outputs(self, usage: Dict) -> None:
        """Download the files the model cited and price its containers."""
        citations = usage.pop("sandbox_citations")
        usage["sandbox_files"] = await OpenAISandboxFiles(
            self.client, self.api_key
        ).download(citations)

    async def _stream_chat_completions(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        effort: Optional[str],
        tools: Optional[List[Dict]] = None,
        allow_tool_calls: bool = True,
    ):
        """
        Stream using the Chat Completions API.

        Chat Completions has no web search tool, so the marker is dropped
        rather than sent as an unknown tool type.

        Args:
            messages: Prepared messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            effort: Reasoning effort, sent when the model supports it
            tools: Optional tools for function calling

        Returns:
            OpenAI Chat Completions stream
        """
        function_tools = [
            tool for tool in tools or [] if tool.get("type") != "web_search"
        ]
        params = self._build_chat_completion_params(
            messages, max_tokens, temperature, effort, function_tools
        )
        if "tools" in params and not allow_tool_calls:
            params["tool_choice"] = "none"

        return await self.client.chat.completions.create(**params)

    def _apply_openai_sampling(
        self,
        params: Dict,
        temperature: float,
        effort: Optional[str] = None,
    ) -> None:
        """Apply generation controls in the OpenAI dialect.

        Each service owns its own dialect. ``output_config`` and ``thinking``
        are Anthropic shapes and belong to ClaudeService; sending either to an
        OpenAI client fails the request as an unknown argument.
        """
        if self.capabilities.supports_temperature:
            params["temperature"] = temperature

        resolved_effort = self.capabilities.resolve_effort(effort)
        if resolved_effort:
            params["reasoning_effort"] = resolved_effort

    def _build_chat_completion_params(
        self,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        effort: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Build parameters for chat completion API call.

        Args:
            messages: List of messages
            max_tokens: Max tokens to generate
            temperature: Temperature setting
            effort: Reasoning effort, sent when the model supports it
            tools: Optional tools for function calling

        Returns:
            Parameters dictionary
        """
        params = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._token_limit(max_tokens),
        }

        self._apply_openai_sampling(params, temperature, effort)

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        return params

    async def _get_structured_completion(
        self, messages: List[Dict], structured_spec: Dict
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
            logger.warning(
                "[OpenAI] No response_format generated, falling back to streaming"
            )
            stream = self.stream_chat_completion(messages)
            return await StreamAggregator.aggregate_stream(stream)

        try:
            response = await self._generate_with_chat_completions_structure(
                messages, response_format
            )
            return SchemaTransformer.extract_field_value(response, structured_spec)
        except Exception as e:
            logger.exception("OpenAI structured output error")
            error_message = OpenAIErrorHandler.format_error(e)
            return f"Error: {error_message}"

    async def _generate_with_chat_completions_structure(
        self, messages: List[Dict], response_format: Dict
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
            **self._token_limit(1024),
        }
        self._apply_openai_sampling(params, 0.0)

        return await self.client.chat.completions.create(**params)

    async def generate_image(
        self,
        prompt: str,
        model: str,
        size: str = "auto",
        quality: str = "auto",
        images: Sequence[Dict] = (),
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Generate an image with a GPT Image model and stream the result like a
        chat completion: a status chunk, then the image bytes and metadata.

        With ``images`` (the turn's attachments as base64 data URLs) the model
        edits or combines them instead of drawing from the prompt alone.
        """
        if not prompt.strip():
            yield "Error: Describe the image you want, or the change to make.", None
            return

        try:
            yield "Generating image...", None

            params = {
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "n": 1,
            }
            if images:
                response = await self.client.images.edit(
                    image=[self._image_upload(image) for image in images], **params
                )
            else:
                response = await self.client.images.generate(**params)

            metadata = {
                "image_bytes": base64.b64decode(response.data[0].b64_json),
                "cost": self._image_generation_cost(response.usage),
                "model": model,
                "size": response.size or size,
                "quality": response.quality or quality,
                # The turn is billed by the quoted cost, not by these counts.
                "input_tokens": 0,
                "output_tokens": 0,
            }

            yield "Image generated successfully", metadata

        except Exception as e:
            logger.exception("Image generation error")
            error_message = OpenAIErrorHandler.format_error(e)
            yield f"Error: {error_message}", None

    @staticmethod
    def _image_upload(image: Dict) -> Tuple[str, bytes, str]:
        """Turn an attachment's base64 data URL into a multipart file upload."""
        encoded = image["preview"].split(",", 1)[1]
        data, mime_type = to_provider_image(
            base64.b64decode(encoded), image["type"], GPT_IMAGE_EDIT_TYPES
        )
        return image["name"], data, mime_type

    @staticmethod
    def _image_generation_cost(usage) -> Decimal:
        """Price a GPT Image call from the token usage OpenAI reports."""
        details = usage.input_tokens_details
        return (
            details.text_tokens * GPT_IMAGE_TEXT_INPUT_RATE
            + details.image_tokens * GPT_IMAGE_IMAGE_INPUT_RATE
            + usage.output_tokens * GPT_IMAGE_OUTPUT_RATE
        ) / Decimal(1_000_000)

    @staticmethod
    def get_web_search_tool() -> Dict:
        """
        Get web search tool indicator for OpenAI.

        OpenAI uses the Responses API with tools=[{"type": "web_search"}].

        Returns:
            Web search tool dictionary
        """
        return OpenAIWebSearchTools.get_tool_definition()
