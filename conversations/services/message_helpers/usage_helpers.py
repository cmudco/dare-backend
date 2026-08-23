"""
Cross-round token-usage accumulation for the tool loop.

Each model call (round) emits its own usage frames; within a round the
latest frame wins (frames carry that call's cumulative numbers), and
totals sum across rounds. The summed totals feed both the mid-stream
billing gate and finalization; the per-round breakdown persists to
``Message.usage_details`` for audit.
"""

from typing import Any, Dict, List, Optional

_TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")
_BREAKDOWN_KEYS = (
    "thinking_tokens",
    "visible_output_tokens",
    "stop_reason",
    "request_max_tokens",
    "effort",
    "thinking_summary",
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
        for key in ("thinking_tokens", "visible_output_tokens"):
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
