"""
Token usage extraction utilities for LLM providers.

This module provides functions to extract and standardize token usage information
from different LLM provider responses.
"""

from typing import Dict, Optional


class UsageExtractor:
    """Base usage extraction utilities."""

    @staticmethod
    def build_usage_dict(
        input_tokens: Optional[int], output_tokens: Optional[int]
    ) -> Optional[Dict]:
        """
        Build standardized usage dictionary.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Usage dictionary or None if tokens not available
        """
        if input_tokens is None or output_tokens is None:
            return None

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class OpenAIUsageExtractor:
    """OpenAI-specific usage extraction."""

    @staticmethod
    def extract_from_chat_completion(chunk) -> Optional[Dict]:
        """
        Extract usage from OpenAI chat completion chunk.

        Args:
            chunk: Chat completion chunk

        Returns:
            Usage dictionary or None
        """
        if not hasattr(chunk, "usage") or chunk.usage is None:
            return None

        return UsageExtractor.build_usage_dict(
            input_tokens=chunk.usage.prompt_tokens,
            output_tokens=chunk.usage.completion_tokens,
        )

    @staticmethod
    def extract_from_responses_api(chunk) -> Optional[Dict]:
        """
        Extract usage from OpenAI Responses API completed event.

        Args:
            chunk: Responses API event chunk

        Returns:
            Usage dictionary or None
        """
        if not hasattr(chunk, "response"):
            return None

        response = chunk.response
        if not hasattr(response, "usage") or response.usage is None:
            return None

        usage_obj = response.usage
        input_tokens = getattr(usage_obj, "input_tokens", None)
        output_tokens = getattr(usage_obj, "output_tokens", None)

        return UsageExtractor.build_usage_dict(input_tokens, output_tokens)


class ClaudeUsageExtractor:
    """Claude-specific usage extraction."""

    def __init__(self):
        """Initialize with state to track input tokens across events."""
        self.input_tokens: Optional[int] = None

    def extract_from_message_start(self, event) -> None:
        """
        Extract input tokens from message_start event.

        Args:
            event: Message start event
        """
        if hasattr(event, "message") and hasattr(event.message, "usage"):
            self.input_tokens = event.message.usage.input_tokens

    def extract_from_message_delta(self, event) -> Optional[Dict]:
        """
        Extract usage from message_delta event.

        Args:
            event: Message delta event

        Returns:
            Usage dictionary or None
        """
        if not hasattr(event, "usage"):
            return None

        output_tokens = event.usage.output_tokens
        if self.input_tokens is None:
            return None

        return UsageExtractor.build_usage_dict(self.input_tokens, output_tokens)

    def reset(self):
        """Reset the state."""
        self.input_tokens = None


class GeminiUsageExtractor:
    """Gemini-specific usage extraction."""

    def __init__(self):
        """Initialize with state to track tokens across chunks."""
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None

    def update_from_chunk(self, chunk) -> None:
        """
        Update token counts from chunk.

        Args:
            chunk: Gemini response chunk
        """
        if not hasattr(chunk, "usage_metadata") or chunk.usage_metadata is None:
            return

        usage = chunk.usage_metadata

        if hasattr(usage, "prompt_token_count"):
            self.input_tokens = usage.prompt_token_count

        if hasattr(usage, "candidates_token_count"):
            self.output_tokens = usage.candidates_token_count

    def get_final_usage(self) -> Optional[Dict]:
        """
        Get final usage dictionary.

        Returns:
            Usage dictionary or None
        """
        return UsageExtractor.build_usage_dict(self.input_tokens, self.output_tokens)

    def reset(self):
        """Reset the state."""
        self.input_tokens = None
        self.output_tokens = None
