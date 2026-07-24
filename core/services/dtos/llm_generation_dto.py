"""
LLM Generation DTOs

Immutable inputs and outputs of the LLM observability service:
- LLMGenerationContext: identity/config snapshot taken when a provider call
  starts (who, which model, which trace).
- LLMGenerationRecord: completed-call snapshot (context + timing, tokens,
  output, error state), ready to emit as a `$ai_generation` event.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LLMGenerationContext:
    """Identity and configuration for one LLM provider call being tracked.

    Args:
        provider: DARE provider slug (e.g. "openai", "claude", "gemini")
        model: Model identifier (e.g. "claude-sonnet-5")
        input_messages: Messages array sent to the LLM
        trace_id: Groups all generations of one chat turn (message id)
        session_id: Groups all traces of one conversation (conversation id)
        distinct_id: PostHog person identifier (user id or "anonymous")
        is_streaming: Whether the call streams chunks or returns in one shot
        extra_properties: Additional custom properties (modes, platform, etc.)
    """

    provider: str
    model: str
    input_messages: List[Dict[str, Any]]
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    distinct_id: Optional[str] = None
    is_streaming: bool = True
    extra_properties: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMGenerationRecord:
    """A single completed LLM generation, ready for analytics capture.

    Args:
        provider: DARE provider slug (e.g. "openai", "claude", "gemini")
        model: Model identifier (e.g. "claude-sonnet-5")
        input_messages: Messages array sent to the LLM
        output: Final assistant text produced by the call
        latency: Wall-clock duration of the call in seconds
        input_tokens: Prompt token count reported by the provider
        output_tokens: Completion token count reported by the provider
        time_to_first_token: Seconds until the first streamed chunk (streaming only)
        is_streaming: Whether the call streamed chunks or returned in one shot
        is_error: Whether the call failed (exception or error sentinel)
        error_message: Error description when is_error is True
        trace_id: Groups all generations of one chat turn (message id)
        session_id: Groups all traces of one conversation (conversation id)
        distinct_id: PostHog person identifier (user id or "anonymous")
        tool_call_count: Number of DARE/MCP tool calls requested by this generation
        extra_properties: Additional custom properties (platform, modes, etc.)
    """

    provider: str
    model: str
    input_messages: List[Dict[str, Any]]
    output: str
    latency: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    time_to_first_token: Optional[float] = None
    is_streaming: bool = True
    is_error: bool = False
    error_message: Optional[str] = None
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    distinct_id: Optional[str] = None
    tool_call_count: int = 0
    extra_properties: Dict[str, Any] = field(default_factory=dict)
