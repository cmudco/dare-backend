"""The panel/council request a chat turn carries, validated once at the boundary."""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

ENSEMBLE_DEPTHS = ("panel", "council")
MIN_RESPONDERS = 2


@dataclass(frozen=True)
class EnsembleRequest:
    depth: str
    responder_ids: Tuple[str, ...]
    chairman_id: str

    @classmethod
    def parse(cls, value: Any) -> Optional["EnsembleRequest"]:
        """Build from the wire payload; None when the turn is single-model."""
        if not isinstance(value, dict):
            return None
        depth = value.get("depth")
        responder_ids = value.get("responder_ids")
        chairman_id = value.get("chairman_id")
        if (
            depth not in ENSEMBLE_DEPTHS
            or not isinstance(responder_ids, list)
            or len(responder_ids) < MIN_RESPONDERS
            or not chairman_id
        ):
            return None
        return cls(
            depth=depth,
            responder_ids=tuple(str(rid) for rid in responder_ids),
            chairman_id=str(chairman_id),
        )
