"""
Cross-round token-usage accumulation for the tool loop.

Each model call (round) emits its own usage frames; within a round the
latest frame wins (frames carry that call's cumulative numbers), and
totals sum across rounds. The summed totals feed both the mid-stream
billing gate and finalization; the per-round breakdown persists to
``Message.usage_details`` for audit.
"""

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional

import tiktoken

_TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")
_BREAKDOWN_KEYS = (
    "thinking_tokens",
    "visible_output_tokens",
    "stop_reason",
    "request_max_tokens",
    "effort",
    "thinking_summary",
    "estimated",
    "cached_input_tokens",
    "cache_write_input_tokens",
)
_SUMMED_OPTIONAL_KEYS = (
    "thinking_tokens",
    "visible_output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
)


class UsageAccumulator:
    """Accumulates per-round usage frames into billable totals."""

    def __init__(self) -> None:
        self._rounds: Dict[int, Dict[str, Any]] = {}

    def observe(self, round_index: int, usage: Optional[Dict[str, Any]]) -> None:
        """Record a usage frame for a round (latest frame wins)."""
        if not usage:
            return
        if not any(usage.get(key) for key in _TOKEN_KEYS) and "cost" not in usage:
            return
        self._rounds[round_index] = usage

    def totals(self) -> Dict[str, Any]:
        """Summed usage across all rounds, in the shape billing expects."""
        totals: Dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        cost = None
        for usage in self._rounds.values():
            for key in _TOKEN_KEYS:
                totals[key] += usage.get(key) or 0
            if usage.get("cost") is not None:
                cost = (cost or 0) + usage["cost"]
        if not totals["total_tokens"]:
            totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        if cost is not None:
            totals["cost"] = cost
        for key in _SUMMED_OPTIONAL_KEYS:
            values = [usage.get(key) for usage in self._rounds.values()]
            if any(value is not None for value in values):
                totals[key] = sum(value or 0 for value in values)
        if self._rounds:
            final_usage = self._rounds[max(self._rounds)]
            for key in ("stop_reason", "request_max_tokens", "effort"):
                if final_usage.get(key) is not None:
                    totals[key] = final_usage[key]
        return totals

    def breakdown(self) -> List[Dict[str, Any]]:
        """Per-round token/cost breakdown for ``Message.usage_details``."""
        return [
            {
                "round": round_index,
                "input_tokens": usage.get("input_tokens") or 0,
                "output_tokens": usage.get("output_tokens") or 0,
                **{
                    key: usage[key]
                    for key in _BREAKDOWN_KEYS
                    if usage.get(key) is not None
                },
                **({"cost": usage["cost"]} if usage.get("cost") is not None else {}),
            }
            for round_index, usage in sorted(self._rounds.items())
        ]

    def has_usage(self) -> bool:
        return bool(self._rounds)

    def round_usage(self, round_index: int) -> Optional[Dict[str, Any]]:
        return self._rounds.get(round_index)


@lru_cache(maxsize=1)
def _encoder():
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return len(_encoder().encode(text, disallowed_special=()))


def estimate_usage(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    output_text: str,
    observed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Usage frame for a call stopped before the provider's final count.

    The provider still bills the full prompt and every token it generated,
    so nothing here may be zero. Provider-reported numbers win where they
    exist (Anthropic reports the prompt at stream start, Gemini reports
    cumulative counts per chunk); the rest is tokenized with a GPT-family
    encoder. The frame is marked ``estimated``.
    """
    observed = dict(observed or {})
    input_tokens = observed.get("input_tokens") or (
        sum(_count_tokens(message) for message in messages)
        + (_count_tokens(tools) if tools else 0)
    )
    output_tokens = max(observed.get("output_tokens") or 0, _count_tokens(output_text))
    observed.pop("provisional", None)
    return {
        **observed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
    }
