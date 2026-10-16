"""Pure recommendation policy for LiteLLM models: background work, vision, agents."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.services.model_identity import supports_vision

MAX_BACKGROUND_MODEL_RECOMMENDATIONS = 4

_LUNA = re.compile(r"(?:^|[/_.-])luna(?:$|[/_.-])", re.IGNORECASE)
_GEMINI = re.compile(r"(?:^|[/_.-])gemini(?:$|[/_.-]|\d)", re.IGNORECASE)
_GEMINI_FLASH = re.compile(r"gemini.*flash", re.IGNORECASE)
_HAIKU = re.compile(r"(?:^|[/_.-])haiku(?:$|[/_.-]|\d)", re.IGNORECASE)
_HAIKU_VERSION = re.compile(
    r"(?<!\d)(?P<major>\d{1,2})(?:[.\-_](?P<minor>\d{1,2}))?(?!\d)"
)
_GEMINI_VERSION = re.compile(
    r"gemini[\W_]*(?P<major>\d+)(?:[\W_]+(?P<minor>\d+))?",
    re.IGNORECASE,
)
_GPT_VERSION = re.compile(
    r"(?:^|[/_.-])gpt-(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:-(?P<tier>[a-z]+))?(?:$|[/_.:])",
    re.IGNORECASE,
)
# Flagship tiers only; mini, nano and pro variants are not chat defaults.
_GPT_TIERS = {"sol": 3, None: 2, "terra": 2, "luna": 1}
_NON_TEXT = re.compile(
    r"(?:^|[/_.-])"
    r"(audio|embedding|image|moderation|realtime|rerank|speech|transcribe|tts|whisper)"
    r"(?:$|[/_.-])",
    re.IGNORECASE,
)


def recommend_background_models(
    models: Iterable[str],
    *,
    limit: int = MAX_BACKGROUND_MODEL_RECOMMENDATIONS,
) -> list[str]:
    """Return a ranked shortlist: Luna, then Gemini Flash, other Gemini, and Haiku."""
    if limit <= 0:
        return []

    candidates = _unique_text_models(models)
    luna = min(
        (model for model in candidates if _LUNA.search(model)),
        key=_canonical_route_rank,
        default=None,
    )
    groups = (
        [luna] if luna else [],
        sorted(
            (model for model in candidates if _GEMINI_FLASH.search(model)),
            key=_gemini_rank,
            reverse=True,
        ),
        sorted(
            (
                model
                for model in candidates
                if _GEMINI.search(model) and not _GEMINI_FLASH.search(model)
            ),
            key=_gemini_rank,
            reverse=True,
        ),
        sorted(
            (model for model in candidates if _HAIKU.search(model)),
            key=_haiku_rank,
            reverse=True,
        ),
    )

    return _merge(groups, limit)


def is_text_model(model: str) -> bool:
    """Whether a model answers chat, as opposed to embedding, audio or images."""
    return not _NON_TEXT.search(model)


@dataclass(frozen=True)
class PricedModel:
    """A chat model and its output price in USD per million tokens, if known."""

    identifier: str
    output_rate: Optional[Decimal]


class AgentModelRole:
    """Why a model is offered for an agent; also the order they are offered in."""

    QUALITY = "quality"
    BALANCED = "balanced"
    BUDGET = "budget"


@dataclass(frozen=True)
class AgentModelPick:
    identifier: str
    role: str


def recommend_agent_models(
    models: Iterable[PricedModel], *, max_output_rate: Decimal
) -> list[AgentModelPick]:
    """Up to three agent models for the user to choose between.

    Quality is the newest flagship GPT, balanced the newest Gemini Flash (or
    other full Gemini), budget the newest GPT Luna (or Gemini Flash-Lite). A
    model without a known price, or priced above ``max_output_rate``, is
    never offered: a newer model is not worth a student's capped allowance
    when it costs several times more.
    """
    affordable = _unique_text_models(
        model.identifier
        for model in models
        if model.output_rate is not None and model.output_rate <= max_output_rate
    )
    gemini = [model for model in affordable if _GEMINI.search(model)]
    full_gemini = [model for model in gemini if "lite" not in model.casefold()]
    picks = (
        (
            AgentModelRole.QUALITY,
            _newest_gpt(affordable, tiers=("sol", None, "terra")),
        ),
        (
            AgentModelRole.BALANCED,
            _newest(
                [m for m in full_gemini if _GEMINI_FLASH.search(m)] or full_gemini,
                _gemini_rank,
            ),
        ),
        (
            AgentModelRole.BUDGET,
            _newest_gpt(affordable, tiers=("luna",))
            or _newest([m for m in gemini if m not in full_gemini], _gemini_rank),
        ),
    )
    return [AgentModelPick(model, role) for role, model in picks if model]


def _newest_gpt(models: list[str], *, tiers) -> Optional[str]:
    return _newest(
        [
            model
            for model in models
            if _gpt_tier(model) is not None and _gpt_tier_name(model) in tiers
        ],
        _gpt_rank,
    )


def _newest(models: list[str], rank) -> Optional[str]:
    return max(models, key=rank, default=None)


def recommend_vision_models(models: Iterable[str]) -> list[str]:
    """Every vision-capable model, best default first: Gemini Flash, Luna, Haiku."""
    candidates = [
        model for model in _unique_text_models(models) if supports_vision(model)
    ]
    groups = (
        sorted(
            (model for model in candidates if _GEMINI_FLASH.search(model)),
            key=_gemini_rank,
            reverse=True,
        ),
        sorted(
            (model for model in candidates if _LUNA.search(model)),
            key=_canonical_route_rank,
        ),
        sorted(
            (model for model in candidates if _HAIKU.search(model)),
            key=_haiku_rank,
            reverse=True,
        ),
        sorted(candidates, key=_canonical_route_rank),
    )
    return _merge(groups)


def _merge(groups: Iterable[Iterable[str]], limit: int | None = None) -> list[str]:
    recommendations: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for model in group:
            normalized = model.casefold()
            if normalized in seen:
                continue
            recommendations.append(model)
            seen.add(normalized)
            if len(recommendations) == limit:
                return recommendations
    return recommendations


def _canonical_route_rank(model: str) -> tuple[int, int, str]:
    """Prefer the clean model ID over provider-prefixed aliases."""
    return model.count("/") + model.count("."), len(model), model.casefold()


def _unique_text_models(models: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw_model in models:
        model = raw_model.strip() if raw_model else ""
        normalized = model.casefold()
        if not model or normalized in seen or _NON_TEXT.search(model):
            continue
        unique.append(model)
        seen.add(normalized)
    return unique


def _gemini_rank(model: str) -> tuple[int, int, int, int, str]:
    match = _GEMINI_VERSION.search(model)
    major = int(match.group("major")) if match else 0
    minor = int(match.group("minor") or 0) if match else 0
    stable = 0 if "preview" in model.casefold() else 1
    full_model = 0 if "lite" in model.casefold() else 1
    return major, minor, stable, full_model, model.casefold()


def _haiku_rank(model: str) -> tuple[int, int, str]:
    match = _HAIKU_VERSION.search(model)
    major = int(match.group("major")) if match else 0
    minor = int(match.group("minor") or 0) if match else 0
    return major, minor, model.casefold()


def _gpt_tier_name(model: str) -> Optional[str]:
    match = _GPT_VERSION.search(model)
    if not match or not match.group("tier"):
        return None
    return match.group("tier").lower()


def _gpt_tier(model: str):
    if not _GPT_VERSION.search(model):
        return None
    return _GPT_TIERS.get(_gpt_tier_name(model))


def _gpt_rank(model: str) -> tuple:
    match = _GPT_VERSION.search(model)
    major, minor = int(match.group("major")), int(match.group("minor") or 0)
    # The clean model id beats provider-prefixed aliases of the same model.
    return major, minor, _gpt_tier(model), -model.count("/"), -len(model)
