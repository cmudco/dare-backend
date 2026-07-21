"""
Stream processing utilities for LLM providers.

This module provides async generators and utilities for processing streaming
responses from different LLM providers. Includes extraction of web search
sources/citations when web search is enabled.
"""

import json
from typing import AsyncGenerator, Dict, Tuple, List, Optional
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
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process OpenAI chat completion stream.

        Args:
            response: OpenAI chat completion stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        current_tool_calls = {}  # Track tool calls by index
        tool_calls_yielded = False

        async for chunk in response:
            # Yield content chunks
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content, None

            # Handle tool calls
            if chunk.choices and chunk.choices[0].delta.tool_calls:
                for tc in chunk.choices[0].delta.tool_calls:
                    idx = tc.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function and tc.function.name else "",
                            "arguments": ""
                        }
                    if tc.function and tc.function.arguments:
                        current_tool_calls[idx]["arguments"] += tc.function.arguments

            # Yield usage data
            usage = OpenAIUsageExtractor.extract_from_chat_completion(chunk)
            if usage:
                # Include tool calls in usage data if present
                if current_tool_calls:
                    usage["tool_calls"] = list(current_tool_calls.values())
                    tool_calls_yielded = True
                yield "", usage

        # Always yield tool calls at end if we have them and haven't yielded yet
        if current_tool_calls and not tool_calls_yielded:
            yield "", {"tool_calls": list(current_tool_calls.values())}

    @staticmethod
    async def process_responses_api_stream(
        response
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process OpenAI Responses API stream.

        Extracts text content, function tool calls, and web search sources
        (when enabled). Sources are included in the final usage data under
        'web_search_sources'; tool calls under 'tool_calls', in the same
        {id, name, arguments} shape the Chat Completions processor emits so the
        downstream tool loop stays provider-agnostic.

        Args:
            response: OpenAI Responses API stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        web_search_extractor = OpenAIWebSearchExtractor()
        tool_calls = []
        tool_calls_yielded = False

        async for chunk in response:
            if not hasattr(chunk, 'type'):
                continue

            # Handle text delta events
            if chunk.type == 'response.output_text.delta':
                if hasattr(chunk, 'delta') and chunk.delta:
                    yield chunk.delta, None

            # A finished function call arrives as a completed output item.
            # Arguments are already fully accumulated by this point.
            if chunk.type == 'response.output_item.done':
                item = getattr(chunk, 'item', None)
                if item is not None and getattr(item, 'type', None) == 'function_call':
                    tool_calls.append({
                        # call_id is what a follow-up turn must echo back;
                        # id identifies the output item itself.
                        "id": getattr(item, 'call_id', None) or getattr(item, 'id', ''),
                        "name": getattr(item, 'name', '') or '',
                        "arguments": getattr(item, 'arguments', '') or '',
                    })

            # Extract web search sources from streaming events
            web_search_extractor.process_chunk(chunk)

            # Handle completion event with usage
            if chunk.type == 'response.completed':
                usage = OpenAIUsageExtractor.extract_from_responses_api(chunk) or {}

                # Include web search sources in usage data
                sources = web_search_extractor.get_sources()
                if sources:
                    usage["web_search_sources"] = sources

                if tool_calls:
                    usage["tool_calls"] = tool_calls
                    tool_calls_yielded = True

                yield "", usage

        # Incomplete responses (e.g. max_output_tokens hit mid-turn) never emit
        # response.completed, so surface any tool calls we did collect.
        if tool_calls and not tool_calls_yielded:
            yield "", {"tool_calls": tool_calls}


class ClaudeStreamProcessor:
    """Claude-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process Claude message stream.

        Extracts text content, tool calls, and web search sources (when enabled).
        Sources are included in the final usage data under 'web_search_sources'.

        Args:
            response: Claude message stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        usage_extractor = ClaudeUsageExtractor()
        web_search_extractor = ClaudeWebSearchExtractor()
        tool_calls = []
        provider_tool_calls = []
        provider_tool_results = []
        current_tool_call = None
        current_provider_tool_call = None
        tool_calls_yielded = False
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
                    yield event.delta.text, None
                # Handle tool input JSON delta
                elif hasattr(event.delta, 'partial_json'):
                    if current_tool_call:
                        current_tool_call["arguments"] += event.delta.partial_json
                    elif current_provider_tool_call:
                        current_provider_tool_call["arguments"] += event.delta.partial_json

            # Handle content block stop (finalize tool call)
            elif event.type == "content_block_stop":
                if current_tool_call:
                    tool_calls.append(current_tool_call)
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

                # Include tool calls in usage data if present
                if tool_calls:
                    usage["tool_calls"] = tool_calls
                    tool_calls_yielded = True
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
                    yield "", usage

        # Always yield tool calls at end if we have them and haven't yielded yet
        if (
            (tool_calls and not tool_calls_yielded)
            or (provider_tool_calls and not provider_tool_calls_yielded)
        ):
            final_data = {}
            if tool_calls and not tool_calls_yielded:
                final_data["tool_calls"] = tool_calls
            if provider_tool_calls and not provider_tool_calls_yielded:
                final_data["provider_tool_calls"] = _merge_provider_tool_calls(
                    provider_tool_calls,
                    provider_tool_results,
                )
            sources = web_search_extractor.get_sources()
            if sources:
                final_data["web_search_sources"] = sources
            yield "", final_data


class GeminiStreamProcessor:
    """Gemini-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process Gemini content stream.

        Uses async iteration for true real-time streaming - chunks are
        yielded as they arrive from the API, not buffered. Extracts web
        search sources from grounding metadata when Google Search is enabled.

        Args:
            response: Gemini async content stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        usage_extractor = GeminiUsageExtractor()
        web_search_extractor = GeminiWebSearchExtractor()
        tool_calls = []
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
                                yield part.text, None

                            # Handle function calls (Gemini's tool calling)
                            # When Gemini uses tools, it returns function_call with content in args
                            if hasattr(part, 'function_call') and part.function_call:
                                fc = part.function_call

                                # Extract content from function call args if present
                                # This handles the case where Gemini wraps content in a tool call
                                if fc.args and 'content' in dict(fc.args):
                                    content = dict(fc.args).get('content', '')
                                    if content:
                                        yield content, None

                                # Also track tool calls for usage data
                                tool_calls.append({
                                    "id": "",  # Gemini doesn't provide IDs
                                    "name": fc.name,
                                    "arguments": json.dumps(dict(fc.args)) if fc.args else "{}"
                                })

                    for result in _extract_gemini_url_metadata(candidate):
                        key = result.get("url") or json.dumps(result, sort_keys=True)
                        url_context_results[key] = result

            # Extract web search sources from grounding metadata (usually in final chunk)
            web_search_extractor.process_chunk(chunk)

            # Update usage metadata
            usage_extractor.update_from_chunk(chunk)

        # Yield final usage data with tool calls and web search sources
        usage = usage_extractor.get_final_usage() or {}
        if tool_calls:
            usage["tool_calls"] = tool_calls
        if url_context_results:
            usage["provider_tool_calls"] = _build_gemini_url_context_tool_calls(
                list(url_context_results.values())
            )

        # Include web search sources in usage data
        sources = web_search_extractor.get_sources()
        if sources:
            usage["web_search_sources"] = sources

        if usage:
            yield "", usage


class StreamAggregator:
    """Utility for aggregating streaming responses into complete text."""

    @staticmethod
    async def aggregate_stream(
        stream: AsyncGenerator[Tuple[str, Dict], None]
    ) -> str:
        """
        Aggregate all text chunks from a stream into a single string.

        Args:
            stream: Async generator yielding (text, usage) tuples

        Returns:
            Complete aggregated text
        """
        response_text = ""
        async for chunk, _ in stream:
            response_text += chunk
        return response_text
