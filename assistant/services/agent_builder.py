"""What the assistant needs to set up agents: the user's chat models, checks
on a proposed agent against them, and creating or removing it.

The assistant offers a few models and web search, and suggests one of the
user's prompts when a text search finds one that fits; the user picks the
model when applying.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db.models import Q

from agents.models import Agent
from assistant.constants import PROMPT_PREVIEW_LENGTH, PROMPT_SEARCH_RESULTS
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
from prompts.models import Prompt

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
    prices = {entry["id"]: _rates(entry) for entry in chat}
    picks = recommend_agent_models(
        (PricedModel(entry["identifier"], prices[entry["id"]][1]) for entry in chat),
        max_output_rate=settings.ASSISTANT_AGENT_MAX_OUTPUT_RATE,
    )
    roles = {pick.identifier: pick.role for pick in picks}
    order = {pick.identifier: index for index, pick in enumerate(picks)}
    chat.sort(key=lambda entry: order.get(entry["identifier"], len(order)))
    return {
        entry["id"]: {
            **entry,
            "input_rate": prices[entry["id"]][0],
            "output_rate": prices[entry["id"]][1],
            "recommended_as": roles.get(entry["identifier"]),
            "accepts_images": (
                images.get(int(entry["id"]), False)
                if entry["id"].isdigit()
                else supports_vision(entry["identifier"])
            ),
        }
        for entry in chat
    }


Rates = Tuple[Optional[Decimal], Optional[Decimal]]


def _rates(entry: Dict[str, Any]) -> Rates:
    """(input, output) USD per million tokens: the DARE model's own rates, or
    the price registry's for a gateway model (gateway entries carry zeros)."""
    if entry["id"].isdigit():
        output = entry["output_token_rate_per_million"]
        if not output:
            return None, None
        return Decimal(str(entry["input_token_rate_per_million"] or 0)), Decimal(
            str(output)
        )
    rates = reference_rates(entry["identifier"])
    if rates is None:
        return None, None
    return rates.input_token_rate_per_million, rates.output_token_rate_per_million


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
                "input_usd_per_million_tokens": _price(entry["input_rate"]),
                "output_usd_per_million_tokens": _price(entry["output_rate"]),
                "accepts_images": entry["accepts_images"],
                "web_search": entry.get("supports_web_search", True),
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


def search_my_prompts(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """The user's prompts whose title or text share words with the query,
    best match first (a title hit counts double)."""
    words = {
        word.lower()
        for word in str(arguments.get("query") or "").split()
        if len(word) >= 3
    }
    if not words:
        return {"success": False, "error": "Give a query of a few keywords."}
    matches = Q()
    for word in words:
        matches |= Q(title__icontains=word) | Q(content__icontains=word)
    scored = []
    for prompt in latest_prompts(user).filter(matches):
        title, content = prompt.title.lower(), prompt.content.lower()
        score = sum(2 * (word in title) + (word in content) for word in words)
        scored.append((score, prompt.updated_at, prompt))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return {
        "success": True,
        "prompts": [
            {
                "prompt_id": prompt.id,
                "title": prompt.title,
                "preview": prompt.content[:PROMPT_PREVIEW_LENGTH],
            }
            for _, _, prompt in scored[:PROMPT_SEARCH_RESULTS]
        ],
    }


def draft_errors(draft: AgentDraft, roster: Roster, user) -> List[str]:
    """Why the user's prompts or models cannot carry this agent; empty if they can."""
    if (
        draft.prompt is not None
        and not latest_prompts(user).filter(pk=draft.prompt.prompt_id).exists()
    ):
        return [
            f"Unknown prompt_id {draft.prompt.prompt_id}. Use one from "
            "search_my_prompts, or send no prompt."
        ]
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


def stored_agent(draft: AgentDraft, roster: Roster, user) -> Dict[str, Any]:
    """The proposed agent as the card shows it and apply later reads it."""
    prompt = None
    if draft.prompt is not None:
        prompt = {
            "id": draft.prompt.prompt_id,
            "title": latest_prompts(user).get(pk=draft.prompt.prompt_id).title,
            "why": draft.prompt.why,
        }
    return {
        "prompt": prompt,
        "options": [
            {
                "model_ref": offer.model_ref,
                "model_name": roster[offer.model_ref]["name"],
                "role": roster[offer.model_ref]["recommended_as"],
                "why": offer.why,
                "input_usd_per_million_tokens": _price(
                    roster[offer.model_ref]["input_rate"]
                ),
                "output_usd_per_million_tokens": _price(
                    roster[offer.model_ref]["output_rate"]
                ),
            }
            for offer in draft.offers
        ],
        "enable_web_search": draft.enable_web_search,
        "web_search_why": draft.web_search_why,
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
    """Create the agent with the chosen model and the suggested prompt, if any."""
    try:
        choice = resolve_model_ref(user, spec["selected_model_ref"])
    except ModelRefUnavailable as exc:
        return {}, [str(exc)]
    notes = []
    prompt = None
    if spec["prompt"] is not None:
        prompt = Prompt.active_objects.filter(
            user=user, pk=spec["prompt"]["id"]
        ).first()
        if prompt is None:
            notes.append("The suggested prompt no longer exists; set one on Agents.")
    agent = Agent.active_objects.create(
        user=user,
        name=name,
        description=description,
        prompt=prompt,
        llm=choice.llm,
        litellm_key=choice.litellm_key,
        litellm_model_name=choice.litellm_model_name,
        enable_web_search=spec["enable_web_search"],
    )
    return {"agent_id": agent.id}, notes


def remove_agent(user, journal: Dict[str, Any]) -> List[str]:
    """Undo create_agent by retiring the agent."""
    agent = Agent.active_objects.filter(user=user, pk=journal["agent_id"]).first()
    if agent is not None:
        agent.soft_delete()
    return []
