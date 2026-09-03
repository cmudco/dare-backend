"""The panel/council request a chat turn carries, validated once at the boundary."""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

ENSEMBLE_DEPTHS = ("panel", "council")
MIN_RESPONDERS = 2
MAX_BRIEF_CHARS = 4000
MAX_ANGLE_CHARS = 300
BRIEF_ROLES = ("responder", "evaluator", "chairman")


def _clean_text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


@dataclass(frozen=True)
class EnsembleBriefs:
    """What each role is told, when the person overrode the defaults.

    ``None`` for a role means its library prompt applies. ``angles`` is
    aligned with the responder line-up; an empty string is no angle.
    """

    responder: Optional[str] = None
    evaluator: Optional[str] = None
    chairman: Optional[str] = None
    angles: Tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: Any, responder_count: int) -> "EnsembleBriefs":
        if not isinstance(value, dict):
            return cls()
        raw_angles = value.get("angles")
        angles = [
            _clean_text(a, MAX_ANGLE_CHARS) or ""
            for a in (raw_angles if isinstance(raw_angles, list) else [])
        ][:responder_count]
        angles += [""] * (responder_count - len(angles))
        return cls(
            responder=_clean_text(value.get("responder"), MAX_BRIEF_CHARS),
            evaluator=_clean_text(value.get("evaluator"), MAX_BRIEF_CHARS),
            chairman=_clean_text(value.get("chairman"), MAX_BRIEF_CHARS),
            angles=tuple(angles),
        )

    def angle_for(self, seat: int) -> str:
        """The angle for the 1-based responder seat, or an empty string."""
        return self.angles[seat - 1] if 0 < seat <= len(self.angles) else ""

    @property
    def is_custom(self) -> bool:
        return any((self.responder, self.evaluator, self.chairman)) or any(self.angles)


@dataclass(frozen=True)
class EnsembleRequest:
    depth: str
    responder_ids: Tuple[str, ...]
    chairman_id: str
    briefs: EnsembleBriefs = field(default_factory=EnsembleBriefs)

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
            briefs=EnsembleBriefs.parse(value.get("briefs"), len(responder_ids)),
        )
