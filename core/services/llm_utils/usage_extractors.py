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

        usage = UsageExtractor.build_usage_dict(
            input_tokens=chunk.usage.prompt_tokens,
            output_tokens=chunk.usage.completion_tokens,
        )
        details = getattr(chunk.usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None)
        if usage is not None and cached:
            usage["cached_input_tokens"] = int(cached)
        return usage

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

        usage = UsageExtractor.build_usage_dict(input_tokens, output_tokens)
        details = getattr(usage_obj, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", None)
        if usage is not None and cached:
            usage["cached_input_tokens"] = int(cached)
        return usage


class ClaudeUsageExtractor:
    """Claude-specific usage extraction."""

    def __init__(self):
        """Initialize with state to track input tokens across events."""
        self.input_tokens: Optional[int] = None
        self.cache_read_tokens: int = 0
        self.cache_write_tokens: int = 0

    def extract_from_message_start(self, event) -> None:
        """
        Extract input tokens from message_start event.

        Anthropic reports the uncached prompt as ``input_tokens`` and the
        cached portions separately; the billable prompt is their sum, so the
        total is folded into ``input_tokens`` to match the other providers.

        Args:
            event: Message start event
        """
        if hasattr(event, "message") and hasattr(event.message, "usage"):
            usage = event.message.usage
            self.cache_read_tokens = int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            )
            self.cache_write_tokens = int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            self.input_tokens = (
                int(usage.input_tokens or 0)
                + self.cache_read_tokens
                + self.cache_write_tokens
            )

    def provisional_usage(self) -> Optional[Dict]:
        """Input-side usage known before any output streams; None until message_start."""
        if self.input_tokens is None:
            return None
        usage = UsageExtractor.build_usage_dict(self.input_tokens, 0)
        if self.cache_read_tokens:
            usage["cached_input_tokens"] = self.cache_read_tokens
        if self.cache_write_tokens:
            usage["cache_write_input_tokens"] = self.cache_write_tokens
        usage["provisional"] = True
        return usage

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

        usage = UsageExtractor.build_usage_dict(self.input_tokens, output_tokens)
        if usage is None:
            return None
        if self.cache_read_tokens:
            usage["cached_input_tokens"] = self.cache_read_tokens
        if self.cache_write_tokens:
            usage["cache_write_input_tokens"] = self.cache_write_tokens

        details = getattr(event.usage, "output_tokens_details", None)
        if isinstance(details, dict):
            thinking_tokens = details.get("thinking_tokens")
        else:
            thinking_tokens = getattr(details, "thinking_tokens", None)

        if thinking_tokens is not None:
            thinking_tokens = max(int(thinking_tokens), 0)
            usage["thinking_tokens"] = thinking_tokens
            # Anthropic documents this subtraction as an approximation of
            # the non-reasoning portion because output_tokens is the inclusive,
            # authoritative billable total.
            usage["visible_output_tokens"] = max(
                int(output_tokens) - thinking_tokens,
                0,
            )

        return usage

    def reset(self):
        """Reset the state."""
        self.input_tokens = None
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0


class GeminiUsageExtractor:
    """Gemini-specific usage extraction."""

    def __init__(self):
        """Initialize with state to track tokens across chunks."""
        self.input_tokens: Optional[int] = None
        self.output_tokens: Optional[int] = None
        self.tool_input_tokens: Optional[int] = None
        self.thinking_tokens: Optional[int] = None
        self.cached_tokens: Optional[int] = None

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

        if hasattr(usage, "tool_use_prompt_token_count"):
            self.tool_input_tokens = usage.tool_use_prompt_token_count

        if hasattr(usage, "thoughts_token_count"):
            self.thinking_tokens = usage.thoughts_token_count

        if hasattr(usage, "cached_content_token_count"):
            self.cached_tokens = usage.cached_content_token_count

    def provisional_usage(self) -> Optional[Dict]:
        """Cumulative usage so far; Gemini reports it on every chunk."""
        usage = self.get_final_usage()
        if usage is not None:
            usage["provisional"] = True
        return usage

    def get_final_usage(self) -> Optional[Dict]:
        """
        Get final usage dictionary.

        Returns:
            Usage dictionary or None
        """
        if self.input_tokens is None or self.output_tokens is None:
            return None

        billable_input_tokens = self.input_tokens + (self.tool_input_tokens or 0)
        # Google bills thinking as output; candidates_token_count excludes it.
        thinking_tokens = self.thinking_tokens or 0
        usage = UsageExtractor.build_usage_dict(
            billable_input_tokens,
            self.output_tokens + thinking_tokens,
        )
        if usage is not None and self.thinking_tokens is not None:
            usage["thinking_tokens"] = thinking_tokens
            usage["visible_output_tokens"] = self.output_tokens
        if usage is not None and self.cached_tokens:
            usage["cached_input_tokens"] = int(self.cached_tokens)
        return usage

    def reset(self):
        """Reset the state."""
        self.input_tokens = None
        self.output_tokens = None
        self.tool_input_tokens = None
        self.thinking_tokens = None
        self.cached_tokens = None
