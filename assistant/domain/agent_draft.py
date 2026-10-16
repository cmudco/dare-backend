"""Validation of a model-written agent setup inside a create_agent action.

The assistant only suggests a model and whether to search the web; the user
picks the agent's prompt themselves. Whether the model is one the user can
use is checked against their roster by the proposal service.
"""

from dataclasses import dataclass
from typing import Any, List, Optional

from assistant.constants import AGENT_DESCRIPTION_MAX_LENGTH


@dataclass(frozen=True)
class AgentDraft:
    description: str
    model_ref: str
    enable_web_search: bool


def parse_agent_draft(raw: Any, label: str, errors: List[str]) -> Optional[AgentDraft]:
    if not isinstance(raw, dict):
        errors.append(f"{label} needs an `agent` object.")
        return None
    model_ref = raw.get("model_ref")
    if not isinstance(model_ref, str) or not model_ref.strip():
        errors.append(f"{label} needs agent.model_ref from list_my_models.")
        return None
    enable_web_search = raw.get("enable_web_search", False)
    if not isinstance(enable_web_search, bool):
        errors.append(f"{label} agent.enable_web_search must be true or false.")
        return None
    description = str(raw.get("description") or "").strip()
    return AgentDraft(
        description=description[:AGENT_DESCRIPTION_MAX_LENGTH],
        model_ref=model_ref.strip(),
        enable_web_search=enable_web_search,
    )
