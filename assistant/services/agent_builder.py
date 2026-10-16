"""What the assistant needs to set up agents: the user's chat models, checks
on a proposed agent against them, and creating or removing it.

The assistant offers a few models and web search; the user picks one of the
models when applying and sets the agent's prompt on the Agents page.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from agents.models import Agent
from assistant.domain.agent_draft import AgentDraft
from billing.litellm_model_policy import (
    PricedModel,
    is_text_model,
    recommend_agent_models,
)
from conversations.models import LLM
from conversations.services.llm_filter_service import filter_for_active_wallet
from core.services.model_identity import supports_vision
from core.services.model_ref import ModelRefUnavailable, resolve_model_ref
from core.services.reference_pricing import reference_rates

Roster = Dict[str, Dict[str, Any]]


class ModelChoiceInvalid(ValueError):
    """The model the user picked is not one of the proposed options."""


def model_roster(user) -> Roster:
    """The chat models the user's active wallet offers, keyed by picker id.

    Recommended models come first, in role order (quality, balanced, budget).
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
    prices = {entry["id"]: _output_rate(entry) for entry in chat}
    picks = recommend_agent_models(
        (PricedModel(entry["identifier"], prices[entry["id"]]) for entry in chat),
        max_output_rate=settings.ASSISTANT_AGENT_MAX_OUTPUT_RATE,
    )
    roles = {pick.identifier: pick.role for pick in picks}
    order = {pick.identifier: index for index, pick in enumerate(picks)}
    chat.sort(key=lambda entry: order.get(entry["identifier"], len(order)))
    return {
        entry["id"]: {
            **entry,
            "output_rate": prices[entry["id"]],
            "recommended_as": roles.get(entry["identifier"]),
            "accepts_images": (
                images.get(int(entry["id"]), False)
                if entry["id"].isdigit()
                else supports_vision(entry["identifier"])
            ),
        }
        for entry in chat
    }


def _output_rate(entry: Dict[str, Any]) -> Optional[Decimal]:
    """USD per million output tokens: the DARE model's own rate, or the price
    registry's for a gateway model (gateway entries carry zero rates)."""
    if entry["id"].isdigit():
        rate = entry["output_token_rate_per_million"]
        return Decimal(str(rate)) if rate else None
    rates = reference_rates(entry["identifier"])
    return rates.output_token_rate_per_million if rates else None


def _price(rate: Optional[Decimal]) -> Optional[str]:
    return None if rate is None else format(rate.normalize(), "f")


def list_my_models(user) -> Dict[str, Any]:
    return {
        "success": True,
        "models": [
            {
                "model_ref": ref,
                "name": entry["name"],
                "recommended_as": entry["recommended_as"],
                "output_usd_per_million_tokens": _price(entry["output_rate"]),
                "accepts_images": entry["accepts_images"],
                "web_search": entry.get("supports_web_search", True),
            }
            for ref, entry in model_roster(user).items()
        ],
    }


def draft_errors(draft: AgentDraft, roster: Roster) -> List[str]:
    """Why the user's models cannot carry this agent; empty if they can."""
    unknown = [ref for ref in draft.model_refs if ref not in roster]
    if unknown:
        return [f"Unknown model_refs {unknown}. Use ones from list_my_models."]
    if draft.enable_web_search:
        offline = [
            roster[ref]["name"]
            for ref in draft.model_refs
            if not roster[ref].get("supports_web_search", True)
        ]
        if offline:
            return [
                f"{', '.join(offline)} cannot search the web; offer models that "
                "can, or leave enable_web_search off."
            ]
    return []


def stored_agent(draft: AgentDraft, roster: Roster) -> Dict[str, Any]:
    """The proposed agent as the card shows it and apply later reads it."""
    return {
        "options": [
            {
                "model_ref": ref,
                "model_name": roster[ref]["name"],
                "role": roster[ref]["recommended_as"],
                "output_usd_per_million_tokens": _price(roster[ref]["output_rate"]),
            }
            for ref in draft.model_refs
        ],
        "enable_web_search": draft.enable_web_search,
        "selected_model_ref": None,
    }


def choose_model(spec: Dict[str, Any], model_ref: Optional[str]) -> None:
    """Record which offered model to create the agent with; the first by default."""
    offered = [option["model_ref"] for option in spec["options"]]
    if model_ref is not None and model_ref not in offered:
        raise ModelChoiceInvalid("That model was not one of the options.")
    spec["selected_model_ref"] = model_ref or offered[0]


def create_agent(
    user, name: str, description: str, spec: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """Create the agent with the chosen model, without a prompt."""
    try:
        choice = resolve_model_ref(user, spec["selected_model_ref"])
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
