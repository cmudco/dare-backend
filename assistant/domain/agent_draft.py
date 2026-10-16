"""Validation of a model-written agent setup inside a create_agent action.

The assistant offers one to three models, each with a sentence on why it
suits the user's task, whether (and why) to search the web, and optionally
one of the user's own prompts it found for the task; the user picks one of
the models when applying. Whether the models and prompt are the user's to
use is checked against their data by the proposal service.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from assistant.constants import (
    AGENT_DESCRIPTION_MAX_LENGTH,
    AGENT_MAX_MODEL_OPTIONS,
    AGENT_REASON_MAX_LENGTH,
)


@dataclass(frozen=True)
class ModelOffer:
    model_ref: str
    why: str


@dataclass(frozen=True)
class PromptSuggestion:
    prompt_id: int
    why: str


@dataclass(frozen=True)
class AgentDraft:
    description: str
    offers: Tuple[ModelOffer, ...]
    enable_web_search: bool
    web_search_why: str
    prompt: Optional[PromptSuggestion]

    @property
    def model_refs(self) -> Tuple[str, ...]:
        return tuple(offer.model_ref for offer in self.offers)


def parse_agent_draft(raw: Any, label: str, errors: List[str]) -> Optional[AgentDraft]:
    if not isinstance(raw, dict):
        errors.append(f"{label} needs an `agent` object.")
        return None
    offers = _offers(raw.get("options"))
    if offers is None:
        errors.append(
            f"{label} needs agent.options: 1-{AGENT_MAX_MODEL_OPTIONS} objects with "
            "a model_ref from list_my_models and a one-sentence `why` it suits "
            "the user's task."
        )
        return None
    enable_web_search = raw.get("enable_web_search", False)
    if not isinstance(enable_web_search, bool):
        errors.append(f"{label} agent.enable_web_search must be true or false.")
        return None
    description = str(raw.get("description") or "").strip()
    web_search_why = str(raw.get("web_search_why") or "").strip()
    return AgentDraft(
        description=description[:AGENT_DESCRIPTION_MAX_LENGTH],
        offers=offers,
        enable_web_search=enable_web_search,
        web_search_why=(
            web_search_why[:AGENT_REASON_MAX_LENGTH] if enable_web_search else ""
        ),
        prompt=_prompt(raw.get("prompt")),
    )


def _prompt(raw: Any) -> Optional[PromptSuggestion]:
    """The suggested prompt, or None; models that fill every field send a zero
    id or a blank reason when they found none."""
    if not isinstance(raw, dict):
        return None
    prompt_id = raw.get("prompt_id")
    why = str(raw.get("why") or "").strip()
    if type(prompt_id) is not int or prompt_id <= 0 or not why:
        return None
    return PromptSuggestion(prompt_id, why[:AGENT_REASON_MAX_LENGTH])


def _offers(raw: Any) -> Optional[Tuple[ModelOffer, ...]]:
    if not isinstance(raw, list) or not 0 < len(raw) <= AGENT_MAX_MODEL_OPTIONS:
        return None
    offers = {}
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        ref = str(entry.get("model_ref") or "").strip()
        why = str(entry.get("why") or "").strip()
        if not ref or not why:
            return None
        offers.setdefault(ref, ModelOffer(ref, why[:AGENT_REASON_MAX_LENGTH]))
    return tuple(offers.values())
