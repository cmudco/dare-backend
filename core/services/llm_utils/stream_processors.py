"""
Stream processing utilities for LLM providers.

This module provides async generators and utilities for processing streaming
responses from different LLM providers.
"""

from typing import AsyncGenerator, Dict, Tuple
from .usage_extractors import (
    OpenAIUsageExtractor,
    ClaudeUsageExtractor,
    GeminiUsageExtractor
)


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

        Args:
            response: OpenAI Responses API stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        async for chunk in response:
            if not hasattr(chunk, 'type'):
                continue

            # Handle text delta events
            if chunk.type == 'response.output_text.delta':
                if hasattr(chunk, 'delta') and chunk.delta:
                    yield chunk.delta, None

            # Handle completion event with usage
            elif chunk.type == 'response.completed':
                usage = OpenAIUsageExtractor.extract_from_responses_api(chunk)
                if usage:
                    yield "", usage


class ClaudeStreamProcessor:
    """Claude-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process Claude message stream.

        Args:
            response: Claude message stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        usage_extractor = ClaudeUsageExtractor()
        tool_calls = []
        current_tool_call = None
        tool_calls_yielded = False

        async for event in response:
            # Handle content block start (for tool use)
            if event.type == "content_block_start":
                if hasattr(event, 'content_block') and event.content_block.type == "tool_use":
                    current_tool_call = {
                        "id": event.content_block.id,
                        "name": event.content_block.name,
                        "arguments": ""
                    }

            # Handle text deltas
            elif event.type == "content_block_delta":
                if hasattr(event.delta, 'text'):
                    yield event.delta.text, None
                # Handle tool input JSON delta
                elif hasattr(event.delta, 'partial_json'):
                    if current_tool_call:
                        current_tool_call["arguments"] += event.delta.partial_json

            # Handle content block stop (finalize tool call)
            elif event.type == "content_block_stop":
                if current_tool_call:
                    tool_calls.append(current_tool_call)
                    current_tool_call = None

            # Extract input tokens from message start
            elif event.type == "message_start":
                usage_extractor.extract_from_message_start(event)

            # Extract usage from message delta
            elif event.type == "message_delta":
                usage = usage_extractor.extract_from_message_delta(event)
                if usage:
                    # Include tool calls in usage data if present
                    if tool_calls:
                        usage["tool_calls"] = tool_calls
                        tool_calls_yielded = True
                    yield "", usage

        # Always yield tool calls at end if we have them and haven't yielded yet
        if tool_calls and not tool_calls_yielded:
            yield "", {"tool_calls": tool_calls}


class GeminiStreamProcessor:
    """Gemini-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process Gemini content stream.

        Uses async iteration for true real-time streaming - chunks are
        yielded as they arrive from the API, not buffered.

        Args:
            response: Gemini async content stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        usage_extractor = GeminiUsageExtractor()
        tool_calls = []

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
                                    "arguments": str(dict(fc.args)) if fc.args else "{}"
                                })

            # Update usage metadata
            usage_extractor.update_from_chunk(chunk)

        # Yield final usage data with tool calls
        usage = usage_extractor.get_final_usage() or {}
        if tool_calls:
            usage["tool_calls"] = tool_calls
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
