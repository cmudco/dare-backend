"""
Stream processing utilities for LLM providers.

This module normalizes every provider's SDK stream into a sequence of
``LLMStreamEvent`` (text deltas, tool-call lifecycle, usage frames).
Tool calls are surfaced the moment the provider reveals them —
``TOOL_CALL_START`` as soon as the name is known, ``TOOL_CALL_ARGS_DELTA``
while arguments stream, ``TOOL_CALL_READY`` once complete — instead of
being buried in a final usage dict. Includes extraction of web search
sources/citations when web search is enabled.
"""

import json
import logging
from typing import AsyncGenerator, Dict, List

from core.services.dtos.stream_event_dto import LLMStreamEvent, StreamEventKind
from core.services.dtos.tool_dto import ToolCallRequest

from .usage_extractors import (
    OpenAIUsageExtractor,
    ClaudeUsageExtractor,
    GeminiUsageExtractor
)
from .web_search_extractors import (
    OpenAIWebSearchExtractor,
    ClaudeWebSearchExtractor,
    GeminiWebSearchExtractor,
)

WEB_FETCH_PREVIEW_CHARS = 4000
logger = logging.getLogger(__name__)


def _safe_get(obj, attr: str, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _safe_to_dict(obj):
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def _sanitize_web_fetch_result(block) -> Dict:
    """Build a compact, persistence-safe result for Anthropic web fetch."""
    content = _safe_get(block, "content")
    content_type = _safe_get(content, "type")
    result = {
        "type": _safe_get(block, "type"),
        "url": _safe_get(content, "url"),
        "title": _safe_get(_safe_get(content, "content"), "title"),
        "retrieved_at": _safe_get(content, "retrieved_at"),
        "error_code": _safe_get(content, "error_code"),
        "result_content_type": _safe_get(_safe_get(content, "content"), "type"),
    }

    if content_type != "web_fetch_result":
        result["content_type"] = content_type
        return {key: value for key, value in result.items() if value is not None}

    document = _safe_get(content, "content")
    source = _safe_get(document, "source")
    source_data = _safe_get(source, "data")

    result.update(
        {
            "content_type": content_type,
            "source_type": _safe_get(source, "type"),
            "media_type": _safe_get(source, "media_type"),
        }
    )

    if isinstance(source_data, str):
        result["content_size"] = len(source_data)
        if _safe_get(source, "type") == "base64":
            result["content_preview"] = "[base64 content omitted]"
            result["truncated"] = True
        else:
            result["content_preview"] = source_data[:WEB_FETCH_PREVIEW_CHARS]
            result["truncated"] = len(source_data) > WEB_FETCH_PREVIEW_CHARS

    return {key: value for key, value in result.items() if value is not None}


def _merge_provider_tool_calls(tool_calls: List[Dict], results: List[Dict]) -> List[Dict]:
    results_by_id = {
        result.get("tool_call_id"): result
        for result in results
        if result.get("tool_call_id")
    }
    merged = []
    for tool_call in tool_calls:
        result = results_by_id.get(tool_call.get("id"))
        merged.append(
            {
                **tool_call,
                "result": result.get("result") if result else None,
                "status": "failed"
                if result and result.get("result", {}).get("error_code")
                else "completed",
            }
        )
    return merged


def _extract_gemini_url_metadata(candidate) -> List[Dict]:
    """Extract URL Context metadata from a Gemini candidate."""
    metadata = _safe_get(candidate, "url_context_metadata") or _safe_get(
        candidate, "urlContextMetadata"
    )
    if not metadata:
        return []

    metadata_dict = _safe_to_dict(metadata)
    url_items = _safe_get(metadata_dict, "url_metadata") or _safe_get(
        metadata_dict, "urlMetadata", []
    )

    results = []
    for item in url_items or []:
        item_dict = _safe_to_dict(item)
        retrieved_url = _safe_get(item_dict, "retrieved_url") or _safe_get(
            item_dict, "retrievedUrl"
        )
        retrieval_status = _safe_get(
            item_dict, "url_retrieval_status"
        ) or _safe_get(item_dict, "urlRetrievalStatus")
        retrieval_status = getattr(retrieval_status, "value", retrieval_status)
        if not retrieved_url and not retrieval_status:
            continue

        results.append(
            {
                "url": retrieved_url,
                "retrieval_status": retrieval_status,
                "content_type": "url_context_result",
                "type": "url_context_result",
            }
        )

    return results


def _build_gemini_url_context_tool_calls(results: List[Dict]) -> List[Dict]:
    """Convert Gemini URL metadata into persisted provider tool calls."""
    tool_calls = []
    for index, result in enumerate(results):
        status = result.get("retrieval_status")
        is_success = status == "URL_RETRIEVAL_STATUS_SUCCESS"
        tool_calls.append(
            {
                "id": f"gemini-url-context-{index + 1}",
                "name": "url_context",
                "arguments": json.dumps({"url": result.get("url")}),
                "provider": "gemini",
                "result": {
                    **result,
                    "error_code": None if is_success else status,
                },
                "status": "completed" if is_success else "failed",
            }
        )
    return tool_calls


class OpenAIStreamProcessor:
    """OpenAI-specific stream processing."""

    @staticmethod
    async def process_chat_completion_stream(
        response
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """
        Process OpenAI chat completion stream.

        Tool-call lifecycle: START is emitted at the first delta that carries
        the function name; READY when the provider reports
        ``finish_reason == "tool_calls"`` (with an end-of-stream fallback).

        Args:
            response: OpenAI chat completion stream

        Yields:
            LLMStreamEvent
        """
        current_tool_calls: Dict[int, Dict[str, str]] = {}  # by delta index
        started_indexes = set()
        ready_indexes = set()

        def _ready_events(indexes):
            for idx in sorted(indexes):
                if idx in ready_indexes:
                    continue
                ready_indexes.add(idx)
                call = current_tool_calls[idx]
                yield LLMStreamEvent.tool_call_ready(
                    ToolCallRequest(
                        id=call["id"], name=call["name"], arguments=call["arguments"]
                    )
                )

        async for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None

            if choice and choice.delta.content:
                yield LLMStreamEvent.text_delta(choice.delta.content)

            if choice and choice.delta.tool_calls:
                for tc in choice.delta.tool_calls:
                    idx = tc.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function and tc.function.name else "",
                            "arguments": ""
                        }
                    call = current_tool_calls[idx]
                    if tc.id and not call["id"]:
                        call["id"] = tc.id
                    if tc.function and tc.function.name and not call["name"]:
                        call["name"] = tc.function.name
                    if idx not in started_indexes and call["name"]:
                        started_indexes.add(idx)
                        yield LLMStreamEvent.tool_call_start(call["id"], call["name"])
                    if tc.function and tc.function.arguments:
                        call["arguments"] += tc.function.arguments
                        yield LLMStreamEvent.tool_call_args_delta(
                            call["id"], tc.function.arguments, len(call["arguments"])
                        )

            if choice and choice.finish_reason == "tool_calls":
                for event in _ready_events(current_tool_calls.keys()):
                    yield event

            usage = OpenAIUsageExtractor.extract_from_chat_completion(chunk)
            if usage:
                yield LLMStreamEvent.usage_frame(usage)

        # Fallback: providers that never send finish_reason == "tool_calls"
        for event in _ready_events(current_tool_calls.keys()):
            yield event

    @staticmethod
    async def process_responses_api_stream(
        response
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """
        Process OpenAI Responses API stream (web search path).

        Extracts both text content and web search sources (when enabled).
        Sources are included in the final usage frame under
        'web_search_sources'. Function tool calls are not part of this path.

        Args:
            response: OpenAI Responses API stream

        Yields:
            LLMStreamEvent
        """
        web_search_extractor = OpenAIWebSearchExtractor()

        async for chunk in response:
            if not hasattr(chunk, 'type'):
                continue

            # Handle text delta events
            if chunk.type == 'response.output_text.delta':
                if hasattr(chunk, 'delta') and chunk.delta:
                    yield LLMStreamEvent.text_delta(chunk.delta)

            # Extract web search sources from streaming events
            web_search_extractor.process_chunk(chunk)

            # Handle completion event with usage
            if chunk.type == 'response.completed':
                usage = OpenAIUsageExtractor.extract_from_responses_api(chunk) or {}

                # Include web search sources in usage data
                sources = web_search_extractor.get_sources()
                if sources:
                    usage["web_search_sources"] = sources

                yield LLMStreamEvent.usage_frame(usage)


class ClaudeStreamProcessor:
    """Claude-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[LLMStreamEvent, None]:
        """
        Process Claude message stream.

        Tool-call lifecycle: Claude announces the tool name and id at
        ``content_block_start``, so START fires before any arguments stream —
        the earliest "the model is writing a tool call" signal of any provider.
        Provider-executed tools (server_tool_use, web fetch) stay inside the
        usage frame under 'provider_tool_calls'.

        Args:
            response: Claude message stream

        Yields:
            LLMStreamEvent
        """
        usage_extractor = ClaudeUsageExtractor()
        web_search_extractor = ClaudeWebSearchExtractor()
        provider_tool_calls = []
        provider_tool_results = []
        current_tool_call = None
        current_provider_tool_call = None
        provider_tool_calls_yielded = False

        async for event in response:
            # Handle content block start (for tool use and web search results)
            if event.type == "content_block_start":
                if hasattr(event, 'content_block'):
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_call = {
                            "id": block.id,
                            "name": block.name,
                            "arguments": ""
                        }
                        yield LLMStreamEvent.tool_call_start(block.id, block.name)
                    elif block.type == "server_tool_use":
                        current_provider_tool_call = {
                            "id": block.id,
                            "name": block.name,
                            "arguments": json.dumps(
                                _safe_to_dict(_safe_get(block, "input", {}))
                            )
                            if _safe_get(block, "input", None)
                            else "",
                            "provider": "anthropic",
                        }
                    elif block.type == "web_fetch_tool_result":
                        provider_tool_results.append(
                            {
                                "tool_call_id": _safe_get(block, "tool_use_id"),
                                "result": _sanitize_web_fetch_result(block),
                            }
                        )
                    # Extract web search sources from tool result blocks
                    web_search_extractor.process_event(event)

            # Handle text deltas
            elif event.type == "content_block_delta":
                if hasattr(event.delta, 'text'):
                    yield LLMStreamEvent.text_delta(event.delta.text)
                # Handle tool input JSON delta
                elif hasattr(event.delta, 'partial_json'):
                    if current_tool_call:
                        current_tool_call["arguments"] += event.delta.partial_json
                        yield LLMStreamEvent.tool_call_args_delta(
                            current_tool_call["id"],
                            event.delta.partial_json,
                            len(current_tool_call["arguments"]),
                        )
                    elif current_provider_tool_call:
                        current_provider_tool_call["arguments"] += event.delta.partial_json

            # Handle content block stop (finalize tool call)
            elif event.type == "content_block_stop":
                if current_tool_call:
                    yield LLMStreamEvent.tool_call_ready(
                        ToolCallRequest(
                            id=current_tool_call["id"],
                            name=current_tool_call["name"],
                            arguments=current_tool_call["arguments"],
                        )
                    )
                    current_tool_call = None
                if current_provider_tool_call:
                    provider_tool_calls.append(current_provider_tool_call)
                    current_provider_tool_call = None

            # Extract input tokens from message start
            elif event.type == "message_start":
                usage_extractor.extract_from_message_start(event)

            # Extract usage from message delta
            elif event.type == "message_delta":
                usage = usage_extractor.extract_from_message_delta(event) or {}
                stop_reason = getattr(event.delta, "stop_reason", None)
                if stop_reason:
                    logger.info(
                        "[ClaudeStreamProcessor] stream stopped: reason=%s, "
                        "output_tokens=%s",
                        stop_reason,
                        usage.get("output_tokens"),
                    )

                if provider_tool_calls:
                    usage["provider_tool_calls"] = _merge_provider_tool_calls(
                        provider_tool_calls,
                        provider_tool_results,
                    )
                    provider_tool_calls_yielded = True

                # Include web search sources in usage data
                sources = web_search_extractor.get_sources()
                if sources:
                    usage["web_search_sources"] = sources

                if usage:
                    yield LLMStreamEvent.usage_frame(usage)

        # Provider tool calls that never made it into a usage frame
        if provider_tool_calls and not provider_tool_calls_yielded:
            final_data = {
                "provider_tool_calls": _merge_provider_tool_calls(
                    provider_tool_calls,
                    provider_tool_results,
                )
            }
            sources = web_search_extractor.get_sources()
            if sources:
                final_data["web_search_sources"] = sources
            yield LLMStreamEvent.usage_frame(final_data)


class GeminiStreamProcessor:
    """Gemini-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[LLMStreamEvent, None]:
        """
        Process Gemini content stream.

        Uses async iteration for true real-time streaming - chunks are
        yielded as they arrive from the API, not buffered. Extracts web
        search sources from grounding metadata when Google Search is enabled.

        Tool-call lifecycle: Gemini delivers function calls whole, so START
        and READY fire back-to-back. Gemini assigns no call ids — the id is
        left empty and synthesized downstream by the tool loop.

        Args:
            response: Gemini async content stream

        Yields:
            LLMStreamEvent
        """
        usage_extractor = GeminiUsageExtractor()
        web_search_extractor = GeminiWebSearchExtractor()
        url_context_results = {}

        # Use async for to properly iterate over async stream
        async for chunk in response:
            # Handle candidates (both text and function calls)
            if hasattr(chunk, 'candidates') and chunk.candidates:
                for candidate in chunk.candidates:
                    if hasattr(candidate, 'content') and candidate.content:
                        for part in candidate.content.parts:
                            # Handle text parts
                            if hasattr(part, 'text') and part.text:
                                yield LLMStreamEvent.text_delta(part.text)

                            # Handle function calls (Gemini's tool calling)
                            # When Gemini uses tools, it returns function_call with content in args
                            if hasattr(part, 'function_call') and part.function_call:
                                fc = part.function_call

                                # Extract content from function call args if present
                                # This handles the case where Gemini wraps content in a tool call
                                if fc.args and 'content' in dict(fc.args):
                                    content = dict(fc.args).get('content', '')
                                    if content:
                                        yield LLMStreamEvent.text_delta(content)

                                arguments = (
                                    json.dumps(dict(fc.args)) if fc.args else "{}"
                                )
                                yield LLMStreamEvent.tool_call_start("", fc.name)
                                yield LLMStreamEvent.tool_call_ready(
                                    ToolCallRequest(
                                        id="",  # Gemini doesn't provide IDs
                                        name=fc.name,
                                        arguments=arguments,
                                    )
                                )

                    for result in _extract_gemini_url_metadata(candidate):
                        key = result.get("url") or json.dumps(result, sort_keys=True)
                        url_context_results[key] = result

            # Extract web search sources from grounding metadata (usually in final chunk)
            web_search_extractor.process_chunk(chunk)

            # Update usage metadata
            usage_extractor.update_from_chunk(chunk)

        # Yield final usage frame with provider tool calls and web search sources
        usage = usage_extractor.get_final_usage() or {}
        if url_context_results:
            usage["provider_tool_calls"] = _build_gemini_url_context_tool_calls(
                list(url_context_results.values())
            )

        # Include web search sources in usage data
        sources = web_search_extractor.get_sources()
        if sources:
            usage["web_search_sources"] = sources

        if usage:
            yield LLMStreamEvent.usage_frame(usage)


class StreamAggregator:
    """Utility for aggregating streaming responses into complete text."""

    @staticmethod
    async def aggregate_stream(
        stream: AsyncGenerator[LLMStreamEvent, None]
    ) -> str:
        """
        Aggregate all text deltas from an event stream into a single string.

        Args:
            stream: Async generator yielding LLMStreamEvent

        Returns:
            Complete aggregated text
        """
        response_text = ""
        async for event in stream:
            if event.kind is StreamEventKind.TEXT_DELTA:
                response_text += event.text
        return response_text
