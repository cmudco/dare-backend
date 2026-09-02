"""Pure recommendation policy for LiteLLM background models."""

import re
from collections.abc import Iterable

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
_NON_TEXT = re.compile(
    r"(?:^|[/_.-])"
    r"(audio|embedding|image|moderation|realtime|rerank|speech|transcrib|tts|whisper)"
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
