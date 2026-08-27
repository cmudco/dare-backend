"""Typed events yielded by the LLM provider streaming layer.

Every provider stream — regardless of the underlying SDK's event shapes —
is normalized into a sequence of ``LLMStreamEvent``. This is what makes a
"the model is writing a tool call" signal possible: tool-call lifecycle is
first-class instead of being buried in a final usage dict.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .tool_dto import ToolCallRequest


class StreamEventKind(Enum):
    """Kinds of events a provider stream can emit."""

    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    THINKING_BLOCK_READY = "thinking_block_ready"
    PROVIDER_CONTENT_BLOCK_READY = "provider_content_block_ready"
    TOOL_CALL_START = "tool_call_start"  # name (and usually id) known, args streaming
    TOOL_CALL_ARGS_DELTA = "tool_call_args_delta"  # argument JSON grew
    TOOL_CALL_READY = "tool_call_ready"  # arguments complete
    USAGE = "usage"  # token usage / provider metadata frame


@dataclass(frozen=True)
class LLMStreamEvent:
    """One normalized event from a provider stream.

    Field usage by kind:
        TEXT_DELTA: ``text``
        TOOL_CALL_START: ``tool_call_id`` (may be "" for Gemini), ``tool_name``
        TOOL_CALL_ARGS_DELTA: ``tool_call_id``, ``args_delta``, ``args_len``
            (cumulative characters — drives FE progress)
        TOOL_CALL_READY: ``tool_call``
        PROVIDER_CONTENT_BLOCK_READY: ``provider_content_block`` and
            ``provider_block_index`` — the provider-native assistant block,
            retained so tool continuations can replay the response verbatim
        USAGE: ``usage`` — token counts plus provider extras
            (provider_tool_calls, web_search_sources, image_bytes, ...)
    """

    kind: StreamEventKind
    text: str = ""
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    args_delta: str = ""
    args_len: int = 0
    tool_call: Optional[ToolCallRequest] = None
    usage: Optional[Dict[str, Any]] = None
    thinking_signature: Optional[str] = None
    provider_thinking_block: Optional[Dict[str, Any]] = None
    provider_content_block: Optional[Dict[str, Any]] = None
    provider_block_index: Optional[int] = None

    @classmethod
    def text_delta(cls, text: str) -> "LLMStreamEvent":
        return cls(kind=StreamEventKind.TEXT_DELTA, text=text)

    @classmethod
    def thinking_delta(cls, summary: str) -> "LLMStreamEvent":
        """Provider-supplied summarized thinking, never private raw reasoning."""
        return cls(kind=StreamEventKind.THINKING_DELTA, text=summary)

    @classmethod
    def thinking_block_ready(cls, summary: str, signature: str) -> "LLMStreamEvent":
        """Complete provider thinking block for unchanged tool-turn replay."""
        return cls(
            kind=StreamEventKind.THINKING_BLOCK_READY,
            text=summary,
            thinking_signature=signature,
            provider_thinking_block={
                "type": "thinking",
                "thinking": summary,
                "signature": signature,
            },
        )

    @classmethod
    def redacted_thinking_block_ready(cls, data: str) -> "LLMStreamEvent":
        """Opaque safety-redacted thinking block for unchanged tool replay."""
        return cls(
            kind=StreamEventKind.THINKING_BLOCK_READY,
            provider_thinking_block={"type": "redacted_thinking", "data": data},
        )

    @classmethod
    def provider_content_block_ready(
        cls, block: Dict[str, Any], index: int
    ) -> "LLMStreamEvent":
        """One complete provider-native assistant content block.

        Anthropic requires the latest assistant message to be echoed unchanged
        when a tool result continues that response. The normalized text/tool
        events are intentionally lossy, so this event carries the exact ordered
        block used only for provider replay.
        """
        return cls(
            kind=StreamEventKind.PROVIDER_CONTENT_BLOCK_READY,
            provider_content_block=block,
            provider_block_index=index,
        )

    @classmethod
    def tool_call_start(cls, tool_call_id: str, tool_name: str) -> "LLMStreamEvent":
        return cls(
            kind=StreamEventKind.TOOL_CALL_START,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )

    @classmethod
    def tool_call_args_delta(
        cls, tool_call_id: str, args_delta: str, args_len: int
    ) -> "LLMStreamEvent":
        return cls(
            kind=StreamEventKind.TOOL_CALL_ARGS_DELTA,
            tool_call_id=tool_call_id,
            args_delta=args_delta,
            args_len=args_len,
        )

    @classmethod
    def tool_call_ready(cls, tool_call: ToolCallRequest) -> "LLMStreamEvent":
        return cls(
            kind=StreamEventKind.TOOL_CALL_READY,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            tool_call=tool_call,
        )

    @classmethod
    def usage_frame(cls, usage: Dict[str, Any]) -> "LLMStreamEvent":
        return cls(kind=StreamEventKind.USAGE, usage=usage)
