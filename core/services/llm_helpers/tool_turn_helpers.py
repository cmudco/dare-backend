"""
Tool-turn message builders (OpenAI wire format).

The internal message schema for tool use is OpenAI's: an assistant turn
carrying ``tool_calls`` followed by one ``role:"tool"`` turn per result.
OpenAI-compatible providers (including the LiteLLM wallet proxy, which
fronts every provider) consume these turns verbatim; Claude and Gemini get
them translated at the provider edge by
``core.services.llm_utils.provider_message_converters``.
"""

import json
import logging
from typing import Any, Dict, List, Sequence

from core.services.dtos.tool_dto import ToolCallRequest, ToolCallResult

logger = logging.getLogger(__name__)

# Cap on how much of a persisted tool result is replayed into history.
TOOL_RESULT_HISTORY_MAX_CHARS = 2000


def synthesize_tool_call_id(message_id: Any, round_index: int, position: int) -> str:
    """Deterministic id for providers that assign none (Gemini)."""
    return f"tc-{message_id}-r{round_index}-{position}"


def build_assistant_tool_call_turn(
    text: str, tool_calls: Sequence[ToolCallRequest]
) -> Dict[str, Any]:
    """Assistant turn announcing the calls the model made this round.

    Gemini's ``thought_signature`` rides along on the call entry when
    present; the Gemini converter reattaches it to the function_call part
    (Gemini 3.x rejects replays without it). Same-turn rounds always run
    on the provider that produced the call, so other providers never see
    the extra key.
    """
    entries = []
    for call in tool_calls:
        entry = {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": call.arguments or "{}",
            },
        }
        if getattr(call, "thought_signature", None):
            entry["thought_signature"] = call.thought_signature
        entries.append(entry)
    return {
        "role": "assistant",
        "content": text or "",
        "tool_calls": entries,
    }


def build_tool_result_turn(result: ToolCallResult) -> Dict[str, Any]:
    """``role:"tool"`` turn carrying one call's outcome back to the model.

    ``content`` is already model-facing text (failures arrive as
    ``"Error: ..."`` from the executor formatting), so no shaping happens
    here. ``name`` is kept because the Gemini converter needs it to build a
    ``function_response`` part.
    """
    return {
        "role": "tool",
        "tool_call_id": result.tool_call_id,
        "name": result.tool_name,
        "content": result.content,
    }


def build_history_tool_turns(
    message_id: Any, tool_call_rows: List[Any]
) -> List[Dict[str, Any]]:
    """Reconstruct structured tool turns from persisted ``MessageToolCall`` rows.

    Rows are grouped by ``round_index`` (legacy rows with 0 form one group)
    and each group becomes an assistant-with-tool_calls turn followed by its
    ``role:"tool"`` result turns — the exact sequence the model originally
    produced, so Claude's tool_use/tool_result adjacency constraint holds on
    replay.

    Args:
        message_id: Id of the owning message (used to synthesize missing
            call ids deterministically).
        tool_call_rows: MessageToolCall rows in created order.

    Returns:
        Flat list of message dicts in internal (OpenAI) format.
    """
    rounds: Dict[int, List[Any]] = {}
    for row in tool_call_rows:
        rounds.setdefault(row.round_index, []).append(row)

    turns: List[Dict[str, Any]] = []
    for round_index in sorted(rounds):
        rows = rounds[round_index]
        requests = []
        results = []
        for position, row in enumerate(rows):
            call_id = row.tool_call_id or synthesize_tool_call_id(
                message_id, round_index, position
            )
            arguments = (
                json.dumps(row.arguments)
                if isinstance(row.arguments, (dict, list))
                else str(row.arguments or "{}")
            )
            requests.append(
                ToolCallRequest(id=call_id, name=row.tool_name, arguments=arguments)
            )
            results.append(
                ToolCallResult(
                    tool_call_id=call_id,
                    tool_name=row.tool_name,
                    origin=row.origin,
                    server_slug=row.server_slug,
                    content=_history_result_text(row),
                )
            )
        turns.append(build_assistant_tool_call_turn("", requests))
        turns.extend(build_tool_result_turn(result) for result in results)

    return turns


def summarize_tool_usage(tool_call_rows: List[Any]) -> str:
    """One-line usage note for messages outside the full-replay window."""
    counts: Dict[str, int] = {}
    for row in tool_call_rows:
        counts[row.tool_name] = counts.get(row.tool_name, 0) + 1
    parts = [
        name if count == 1 else f"{name} x{count}"
        for name, count in sorted(counts.items())
    ]
    return f"[used tools: {', '.join(parts)}]"


def _history_result_text(row: Any) -> str:
    """Model-facing result text for a persisted tool call, truncated.

    Never returns an empty string: an empty ``role:"tool"`` turn risks being
    dropped by empty-content message filters, which orphans its parent
    assistant ``tool_calls`` turn and makes providers reject the whole
    request (tool_use without a matching tool_result).
    """
    if row.status == "failed" or (not row.result and row.error):
        return f"Error: {row.error or 'tool execution failed'}"
    text = row.result or ""
    if not text.strip():
        return "(tool completed with no output)"
    if len(text) > TOOL_RESULT_HISTORY_MAX_CHARS:
        text = (
            text[:TOOL_RESULT_HISTORY_MAX_CHARS]
            + f"\n... [truncated, {len(text)} total chars]"
        )
    return text
