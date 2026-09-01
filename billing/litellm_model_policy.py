"""Pure recommendation policy for LiteLLM background models."""

import re
from collections.abc import Iterable
from typing import Optional

_LUNA = re.compile(r"gpt[\W_]*5[\W_]*6[\W_]*luna", re.IGNORECASE)
_GEMINI_FLASH = re.compile(r"gemini.*flash", re.IGNORECASE)
_GEMINI_VERSION = re.compile(
    r"gemini[\W_]*(?P<major>\d+)(?:[\W_]+(?P<minor>\d+))?",
    re.IGNORECASE,
)
_LIGHTWEIGHT = re.compile(
    r"(?:^|[/_.-])(haiku|mini|nano|lite|small)(?:$|[/_.-])",
    re.IGNORECASE,
)
_NON_TEXT = re.compile(
    r"(?:^|[/_.-])"
    r"(audio|embedding|image|moderation|realtime|rerank|speech|transcrib|tts|whisper)"
    r"(?:$|[/_.-])",
    re.IGNORECASE,
)


def recommend_background_model(models: Iterable[str]) -> Optional[str]:
    """Return Luna, the newest Gemini Flash, then a lightweight text model."""
    candidates = [model.strip() for model in models if model and model.strip()]
    text_models = [model for model in candidates if not _NON_TEXT.search(model)]

    luna = next((model for model in text_models if _LUNA.search(model)), None)
    if luna:
        return luna

    flashes = [model for model in text_models if _GEMINI_FLASH.search(model)]
    if flashes:
        return max(flashes, key=_gemini_flash_rank)

    lightweight = next(
        (model for model in text_models if _LIGHTWEIGHT.search(model)), None
    )
    return lightweight or next(iter(text_models), None)


def _gemini_flash_rank(model: str) -> tuple[int, int, int, int]:
    match = _GEMINI_VERSION.search(model)
    major = int(match.group("major")) if match else 0
    minor = int(match.group("minor") or 0) if match else 0
    stable = 0 if "preview" in model.casefold() else 1
    full_flash = 0 if "lite" in model.casefold() else 1
    return major, minor, stable, full_flash
