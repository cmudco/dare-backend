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
        async for chunk in response:
            # Yield content chunks
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content, None

            # Yield usage data
            usage = OpenAIUsageExtractor.extract_from_chat_completion(chunk)
            if usage:
                yield "", usage

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

        async for event in response:
            # Handle text deltas
            if event.type == "content_block_delta":
                if hasattr(event.delta, 'text'):
                    yield event.delta.text, None

            # Extract input tokens from message start
            elif event.type == "message_start":
                usage_extractor.extract_from_message_start(event)

            # Extract usage from message delta
            elif event.type == "message_delta":
                usage = usage_extractor.extract_from_message_delta(event)
                if usage:
                    yield "", usage

        # Ensure we yield something at the end even if no usage
        if usage_extractor.input_tokens is None:
            yield "", None


class GeminiStreamProcessor:
    """Gemini-specific stream processing."""

    @staticmethod
    async def process_stream(response) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Process Gemini content stream.

        Args:
            response: Gemini content stream

        Yields:
            Tuple of (text_chunk, usage_data)
        """
        usage_extractor = GeminiUsageExtractor()

        for chunk in response:
            # Yield text content
            if hasattr(chunk, 'text') and chunk.text:
                yield chunk.text, None

            # Update usage metadata
            usage_extractor.update_from_chunk(chunk)

        # Yield final usage data
        usage = usage_extractor.get_final_usage()
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
