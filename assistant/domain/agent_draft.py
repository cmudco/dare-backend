"""Validation of a model-written agent setup inside a create_agent action.

Pure checks on shape and ranges; whether the prompt and model belong to the
user is checked against their data by the proposal service.
"""

from dataclasses import dataclass
from typing import Any, List, Optional

from assistant.constants import AGENT_DESCRIPTION_MAX_LENGTH, AGENT_PROMPT_MAX_LENGTH
from conversations.constants import ModelEffort

TEMPERATURE_RANGE = (0.0, 1.0)
MAX_TOKENS_RANGE = (256, 65536)
HISTORY_LIMIT_RANGE = (1, 50)
TOGGLES = (
    "enable_web_search",
    "web_fetch_enabled",
    "artifacts_enabled",
    "memory_enabled",
)


@dataclass(frozen=True)
class AgentDraft:
    """An agent the assistant proposes; None settings keep the agent defaults."""

    description: str
    model_ref: str
    prompt_id: Optional[int]
    new_prompt_title: str
    new_prompt_content: str
    temperature: Optional[float]
    effort: Optional[str]
    max_tokens: Optional[int]
    history_limit: Optional[int]
    enable_web_search: bool
    web_fetch_enabled: bool
    artifacts_enabled: bool
    memory_enabled: bool


def parse_agent_draft(raw: Any, label: str, errors: List[str]) -> Optional[AgentDraft]:
    if not isinstance(raw, dict):
        errors.append(f"{label} needs an `agent` object.")
        return None
    found = len(errors)

    model_ref = raw.get("model_ref")
    if not isinstance(model_ref, str) or not model_ref.strip():
        errors.append(f"{label} needs agent.model_ref from list_my_models.")

    prompt_id = raw.get("prompt_id")
    new_prompt = raw.get("new_prompt")
    # Models that fill every schema field send an empty new_prompt beside prompt_id.
    if isinstance(new_prompt, dict) and not any(
        str(new_prompt.get(key) or "").strip() for key in ("title", "content")
    ):
        new_prompt = None
    title = content = ""
    if (prompt_id is None) == (new_prompt is None):
        errors.append(
            f"{label} needs exactly one of agent.prompt_id (from list_my_prompts) "
            "or agent.new_prompt."
        )
    elif prompt_id is not None and type(prompt_id) is not int:
        errors.append(f"{label} agent.prompt_id must be an integer.")
    elif new_prompt is not None:
        if not isinstance(new_prompt, dict):
            errors.append(f"{label} agent.new_prompt must be an object.")
        else:
            title = str(new_prompt.get("title") or "").strip()
            content = str(new_prompt.get("content") or "").strip()
            if not 0 < len(title) <= 255:
                errors.append(f"{label} agent.new_prompt.title needs 1-255 characters.")
            if not 0 < len(content) <= AGENT_PROMPT_MAX_LENGTH:
                errors.append(
                    f"{label} agent.new_prompt.content needs 1-"
                    f"{AGENT_PROMPT_MAX_LENGTH} characters."
                )

    temperature = _number(raw, "temperature", float, TEMPERATURE_RANGE, label, errors)
    max_tokens = _number(raw, "max_tokens", int, MAX_TOKENS_RANGE, label, errors)
    history_limit = _number(
        raw, "history_limit", int, HISTORY_LIMIT_RANGE, label, errors
    )
    effort = raw.get("effort")
    if effort is not None and effort not in ModelEffort.values:
        errors.append(
            f"{label} agent.effort must be one of {list(ModelEffort.values)}."
        )
    toggles = {}
    for toggle in TOGGLES:
        value = raw.get(toggle, False)
        if not isinstance(value, bool):
            errors.append(f"{label} agent.{toggle} must be true or false.")
        toggles[toggle] = value is True

    if len(errors) > found:
        return None
    description = str(raw.get("description") or "").strip()
    return AgentDraft(
        description=description[:AGENT_DESCRIPTION_MAX_LENGTH],
        model_ref=model_ref.strip(),
        prompt_id=prompt_id,
        new_prompt_title=title,
        new_prompt_content=content,
        temperature=temperature,
        effort=effort,
        max_tokens=max_tokens,
        history_limit=history_limit,
        **toggles,
    )


def _number(raw, key, kind, bounds, label, errors):
    value = raw.get(key)
    if value is None:
        return None
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not numeric or (kind is int and not float(value).is_integer()):
        errors.append(f"{label} agent.{key} must be a number.")
        return None
    low, high = bounds
    if not low <= value <= high:
        errors.append(f"{label} agent.{key} must be between {low} and {high}.")
        return None
    return kind(value)
