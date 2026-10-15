"""What the assistant needs to set up agents: the user's prompts and models,
checks on a proposed agent against them, and creating or removing it.
"""

from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction

from agents.models import Agent
from assistant.constants import LIST_PROMPTS_MAX, PROMPT_PREVIEW_LENGTH
from assistant.domain.agent_draft import AgentDraft
from conversations.models import LLM, Conversation
from conversations.services.llm_filter_service import filter_for_active_wallet
from core.services.model_identity import supports_vision
from core.services.model_ref import ModelRefUnavailable, resolve_model_ref
from projects.models import PersonalProject
from prompts.models import Prompt
from prompts.services.prompt_service import PromptService

# Providers whose native web fetch DARE wires up; mirrors the chat settings panel.
WEB_FETCH_PROVIDERS = frozenset({"claude", "gemini"})

Roster = Dict[str, Dict[str, Any]]


def model_roster(user) -> Roster:
    """The chat models the user's active wallet offers, keyed by picker id."""
    catalog = LLM.visible_for_user(user).order_by("tier", "name")
    entries, _ = filter_for_active_wallet(user, catalog)
    images = dict(catalog.values_list("pk", "supports_vision"))
    roster = {}
    for entry in entries:
        if entry["is_image_generator"] or entry["is_audio_transcriber"]:
            continue
        accepts_images = (
            images.get(int(entry["id"]), False)
            if entry["id"].isdigit()
            else supports_vision(entry["identifier"])
        )
        roster[entry["id"]] = {**entry, "accepts_images": accepts_images}
    return roster


def list_my_models(user) -> Dict[str, Any]:
    return {
        "success": True,
        "models": [
            {
                "model_ref": ref,
                "name": entry["name"],
                "accepts_images": entry["accepts_images"],
                "web_search": entry.get("supports_web_search", True),
                "web_fetch": entry["provider"] in WEB_FETCH_PROVIDERS,
                "temperature": entry["supports_temperature"],
                "effort": entry["supports_effort"],
                "reasoning": entry["is_reasoning"],
            }
            for ref, entry in model_roster(user).items()
        ],
    }


def latest_prompts(user):
    """The user's prompts, newest version of each (editing a prompt versions it)."""
    replaced = Prompt.active_objects.filter(user=user, parent__isnull=False).values(
        "parent_id"
    )
    return Prompt.active_objects.filter(user=user).exclude(pk__in=replaced)


def list_my_prompts(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    prompts = latest_prompts(user)
    title_contains = str(arguments.get("title_contains") or "").strip()
    if title_contains:
        prompts = prompts.filter(title__icontains=title_contains)
    rows = prompts.order_by("-updated_at")[:LIST_PROMPTS_MAX]
    return {
        "success": True,
        "total_matching": prompts.count(),
        "prompts": [
            {
                "id": prompt.id,
                "title": prompt.title,
                "preview": prompt.content[:PROMPT_PREVIEW_LENGTH],
                "updated_at": prompt.updated_at.date().isoformat(),
            }
            for prompt in rows
        ],
    }


def draft_errors(draft: AgentDraft, user, roster: Roster) -> List[str]:
    """Why the user's prompts or models cannot carry this agent; empty if they can."""
    errors = []
    if (
        draft.prompt_id is not None
        and not latest_prompts(user).filter(pk=draft.prompt_id).exists()
    ):
        errors.append(
            f"Unknown prompt id {draft.prompt_id}. Use an id from list_my_prompts."
        )
    model = roster.get(draft.model_ref)
    if model is None:
        errors.append(
            f"Unknown model_ref {draft.model_ref!r}. Use one from list_my_models."
        )
        return errors
    name = model["name"]
    if draft.enable_web_search and not model.get("supports_web_search", True):
        errors.append(f"{name} cannot search the web; leave enable_web_search off.")
    if draft.web_fetch_enabled and model["provider"] not in WEB_FETCH_PROVIDERS:
        errors.append(f"{name} cannot fetch pages; leave web_fetch_enabled off.")
    return errors


def stored_agent(draft: AgentDraft, user, roster: Roster) -> Dict[str, Any]:
    """The proposed agent as the card shows it and apply later reads it.

    Temperature and effort the model does not take are dropped: chat would
    ignore them, and models that fill every field send them regardless.
    """
    model = roster[draft.model_ref]
    prompt_title = (
        draft.new_prompt_title
        if draft.prompt_id is None
        else latest_prompts(user).get(pk=draft.prompt_id).title
    )
    return {
        "model_ref": draft.model_ref,
        "model_name": model["name"],
        "prompt_id": draft.prompt_id,
        "prompt_title": prompt_title,
        "new_prompt": (
            {"title": draft.new_prompt_title, "content": draft.new_prompt_content}
            if draft.prompt_id is None
            else None
        ),
        "temperature": draft.temperature if model["supports_temperature"] else None,
        "effort": draft.effort if model["supports_effort"] else None,
        "max_tokens": draft.max_tokens,
        "history_limit": draft.history_limit,
        "enable_web_search": draft.enable_web_search,
        "web_fetch_enabled": draft.web_fetch_enabled,
        "artifacts_enabled": draft.artifacts_enabled,
        "memory_enabled": draft.memory_enabled,
    }


_OPTIONAL_SETTINGS = ("temperature", "effort", "max_tokens", "history_limit")
_TOGGLES = (
    "enable_web_search",
    "web_fetch_enabled",
    "artifacts_enabled",
    "memory_enabled",
)


def create_agent(
    user, name: str, description: str, spec: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """Create the proposed agent (and its new prompt); returns journal and notes."""
    try:
        choice = resolve_model_ref(user, spec["model_ref"])
    except ModelRefUnavailable as exc:
        return {}, [str(exc)]
    with transaction.atomic():
        new_prompt = spec["new_prompt"]
        if new_prompt:
            prompt = PromptService.create_prompt(
                {"title": new_prompt["title"], "content": new_prompt["content"]},
                user,
                is_default=False,
            )
        else:
            prompt = Prompt.active_objects.filter(
                user=user, pk=spec["prompt_id"]
            ).first()
            if prompt is None:
                return {}, ["The prompt no longer exists."]
        agent = Agent.active_objects.create(
            user=user,
            name=name,
            description=description,
            prompt=prompt,
            llm=choice.llm,
            litellm_key=choice.litellm_key,
            litellm_model_name=choice.litellm_model_name,
            **{key: spec[key] for key in _TOGGLES},
            **{key: spec[key] for key in _OPTIONAL_SETTINGS if spec[key] is not None},
        )
    return {
        "agent_id": agent.id,
        "created_prompt_id": prompt.id if new_prompt else None,
    }, []


def remove_agent(user, journal: Dict[str, Any]) -> List[str]:
    """Undo create_agent: retire the agent, and its prompt unless something uses it."""
    agent = Agent.active_objects.filter(user=user, pk=journal["agent_id"]).first()
    if agent is not None:
        agent.soft_delete()
    prompt_id: Optional[int] = journal["created_prompt_id"]
    if prompt_id is None:
        return []
    prompt = Prompt.active_objects.filter(user=user, pk=prompt_id).first()
    if prompt is None:
        return []
    if _prompt_in_use(prompt):
        return ["The new prompt was kept because something else now uses it."]
    prompt.soft_delete()
    return []


def _prompt_in_use(prompt: Prompt) -> bool:
    return (
        Agent.active_objects.filter(prompt=prompt).exists()
        or Conversation.active_objects.filter(prompt=prompt).exists()
        or PersonalProject.active_objects.filter(prompt=prompt).exists()
        or Prompt.active_objects.filter(parent=prompt).exists()
    )
