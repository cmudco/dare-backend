"""What the assistant needs to set up agents: the user's chat models, checks
on a proposed agent against them, and creating or removing it.

The assistant suggests a model and web search only; the user sets the
agent's prompt on the Agents page.
"""

from typing import Any, Dict, List, Tuple

from agents.models import Agent
from assistant.domain.agent_draft import AgentDraft
from billing.litellm_model_policy import is_text_model, recommend_agent_models
from conversations.models import LLM
from conversations.services.llm_filter_service import filter_for_active_wallet
from core.services.model_identity import supports_vision
from core.services.model_ref import ModelRefUnavailable, resolve_model_ref

Roster = Dict[str, Dict[str, Any]]


def model_roster(user) -> Roster:
    """The chat models the user's active wallet offers, keyed by picker id.

    Recommended models (the newest flagship GPT and Gemini) come first.
    """
    catalog = LLM.visible_for_user(user).order_by("tier", "name")
    entries, _ = filter_for_active_wallet(user, catalog)
    images = dict(catalog.values_list("pk", "supports_vision"))
    chat = [
        entry
        for entry in entries
        if not entry["is_image_generator"]
        and not entry["is_audio_transcriber"]
        and is_text_model(entry["identifier"])
    ]
    recommended = set(recommend_agent_models(entry["identifier"] for entry in chat))
    chat.sort(key=lambda entry: entry["identifier"] not in recommended)
    return {
        entry["id"]: {
            **entry,
            "recommended": entry["identifier"] in recommended,
            "accepts_images": (
                images.get(int(entry["id"]), False)
                if entry["id"].isdigit()
                else supports_vision(entry["identifier"])
            ),
        }
        for entry in chat
    }


def list_my_models(user) -> Dict[str, Any]:
    return {
        "success": True,
        "models": [
            {
                "model_ref": ref,
                "name": entry["name"],
                "recommended": entry["recommended"],
                "accepts_images": entry["accepts_images"],
                "web_search": entry.get("supports_web_search", True),
            }
            for ref, entry in model_roster(user).items()
        ],
    }


def draft_errors(draft: AgentDraft, roster: Roster) -> List[str]:
    """Why the user's models cannot carry this agent; empty if they can."""
    model = roster.get(draft.model_ref)
    if model is None:
        return [f"Unknown model_ref {draft.model_ref!r}. Use one from list_my_models."]
    if draft.enable_web_search and not model.get("supports_web_search", True):
        return [f"{model['name']} cannot search the web; leave enable_web_search off."]
    return []


def stored_agent(draft: AgentDraft, roster: Roster) -> Dict[str, Any]:
    """The proposed agent as the card shows it and apply later reads it."""
    return {
        "model_ref": draft.model_ref,
        "model_name": roster[draft.model_ref]["name"],
        "enable_web_search": draft.enable_web_search,
    }


def create_agent(
    user, name: str, description: str, spec: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """Create the proposed agent, without a prompt; returns journal and notes."""
    try:
        choice = resolve_model_ref(user, spec["model_ref"])
    except ModelRefUnavailable as exc:
        return {}, [str(exc)]
    agent = Agent.active_objects.create(
        user=user,
        name=name,
        description=description,
        llm=choice.llm,
        litellm_key=choice.litellm_key,
        litellm_model_name=choice.litellm_model_name,
        enable_web_search=spec["enable_web_search"],
    )
    return {"agent_id": agent.id}, []


def remove_agent(user, journal: Dict[str, Any]) -> List[str]:
    """Undo create_agent by retiring the agent."""
    agent = Agent.active_objects.filter(user=user, pk=journal["agent_id"]).first()
    if agent is not None:
        agent.soft_delete()
    return []
