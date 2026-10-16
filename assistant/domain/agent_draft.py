"""Validation of a model-written agent setup inside a create_agent action.

The assistant offers one to three models and whether to search the web; the
user picks one of the models when applying, and the prompt themselves.
Whether the models are ones the user can use is checked against their
roster by the proposal service.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from assistant.constants import AGENT_DESCRIPTION_MAX_LENGTH, AGENT_MAX_MODEL_OPTIONS


@dataclass(frozen=True)
class AgentDraft:
    description: str
    model_refs: Tuple[str, ...]
    enable_web_search: bool


def parse_agent_draft(raw: Any, label: str, errors: List[str]) -> Optional[AgentDraft]:
    if not isinstance(raw, dict):
        errors.append(f"{label} needs an `agent` object.")
        return None
    refs = raw.get("model_refs")
    if (
        not isinstance(refs, list)
        or not 0 < len(refs) <= AGENT_MAX_MODEL_OPTIONS
        or not all(isinstance(ref, str) and ref.strip() for ref in refs)
    ):
        errors.append(
            f"{label} needs agent.model_refs: 1-{AGENT_MAX_MODEL_OPTIONS} "
            "model_refs from list_my_models."
        )
        return None
    enable_web_search = raw.get("enable_web_search", False)
    if not isinstance(enable_web_search, bool):
        errors.append(f"{label} agent.enable_web_search must be true or false.")
        return None
    description = str(raw.get("description") or "").strip()
    return AgentDraft(
        description=description[:AGENT_DESCRIPTION_MAX_LENGTH],
        model_refs=tuple(dict.fromkeys(ref.strip() for ref in refs)),
        enable_web_search=enable_web_search,
    )
