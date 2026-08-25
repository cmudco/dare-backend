"""Helpers for model capability-aware provider request parameters."""

from dataclasses import dataclass
from typing import Any, Optional

from conversations.constants import ModelEffort
from core.services.model_identity import ModelFamily, resolve_family

EFFORT_VALUES = {choice.value for choice in ModelEffort}


def family_supports_temperature(
    family: Optional[ModelFamily], is_reasoning: bool
) -> bool:
    """Temperature support for a model with no explicit flag of its own."""
    if is_reasoning:
        return False
    return family.supports_temperature if family else True


@dataclass(frozen=True)
class ModelCapabilities:
    """Provider request capabilities for one resolved model."""

    supports_temperature: bool = True
    supports_effort: bool = False
    supports_adaptive_thinking: bool = False
    default_effort: str = ModelEffort.HIGH.value
    default_adaptive_thinking_enabled: bool = False

    @classmethod
    def from_llm(cls, llm: Any) -> "ModelCapabilities":
        """Build capability data from a real or synthetic LLM-shaped object."""
        identifier = getattr(llm, "identifier", "")
        is_reasoning = bool(getattr(llm, "is_reasoning", False))
        supports_temperature = getattr(llm, "supports_temperature", None)
        supports_effort = getattr(llm, "supports_effort", None)
        supports_adaptive_thinking = getattr(llm, "supports_adaptive_thinking", None)

        family = resolve_family(identifier)
        return cls(
            supports_temperature=(
                bool(supports_temperature)
                if supports_temperature is not None
                else family_supports_temperature(family, is_reasoning)
            ),
            supports_effort=(
                bool(supports_effort)
                if supports_effort is not None
                else bool(family and family.supports_effort)
            ),
            supports_adaptive_thinking=(
                bool(supports_adaptive_thinking)
                if supports_adaptive_thinking is not None
                else bool(family and family.supports_adaptive_thinking)
            ),
            default_effort=normalize_effort(
                getattr(llm, "default_effort", None), ModelEffort.HIGH.value
            ),
            default_adaptive_thinking_enabled=bool(
                getattr(llm, "default_adaptive_thinking_enabled", False)
            ),
        )

    def resolve_effort(self, requested_effort: Optional[str]) -> Optional[str]:
        """Return the effort to send for this model, if effort is supported."""
        if not self.supports_effort:
            return None
        return normalize_effort(requested_effort, self.default_effort)


def normalize_effort(value: Optional[str], default: str) -> str:
    """Normalize an effort value to a supported choice."""
    if value in EFFORT_VALUES:
        return value
    if default in EFFORT_VALUES:
        return default
    return ModelEffort.HIGH.value
