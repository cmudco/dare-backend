"""
Cached wrapper around `probe_litellm_connection` for the model picker.

The picker calls this on every `?wallet_scope=` request that resolves to a
LITELLM wallet. Probing is a network round-trip to the LiteLLM proxy; we
cache the probed model list for 5 minutes (model rosters are stable) and
fall back to the last-known-good cache entry when a fresh probe fails so
the picker can still render with a `staleProbe` warning.

Cache keys:
    litellm_probe:<key_id>          -> fresh entry, 5min TTL
    litellm_probe_stale:<key_id>    -> last-good fallback, no TTL (overwritten on fresh hit)

Invalidation is fired from `LiteLLMKey.save()` and `LiteLLMKey.delete()`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from django.core.cache import cache

from billing.litellm_probe import ProbedModel, probe_litellm_connection

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 5 * 60
FRESH_KEY_TEMPLATE = "litellm_probe:{key_id}"
STALE_KEY_TEMPLATE = "litellm_probe_stale:{key_id}"


@dataclass(frozen=True)
class CachedProbe:
    """Model list returned to the picker."""
    models: List[ProbedModel]
    is_stale: bool
    error: Optional[str]


def _fresh_key(key_id: str) -> str:
    return FRESH_KEY_TEMPLATE.format(key_id=key_id)


def _stale_key(key_id: str) -> str:
    return STALE_KEY_TEMPLATE.format(key_id=key_id)


def list_models(litellm_key) -> CachedProbe:
    """Return probed models for a LiteLLMKey, with caching + stale fallback."""
    cache_id = str(litellm_key.pk)
    fresh = cache.get(_fresh_key(cache_id))
    if fresh is not None:
        return CachedProbe(models=fresh, is_stale=False, error=None)

    result = probe_litellm_connection(litellm_key.base_url, litellm_key.api_key)
    if result.ok:
        cache.set(_fresh_key(cache_id), result.models, CACHE_TTL_SECONDS)
        cache.set(_stale_key(cache_id), result.models, None)
        return CachedProbe(models=result.models, is_stale=False, error=None)

    logger.warning(
        "LiteLLM probe failed for key=%s: %s", cache_id, result.error
    )
    stale = cache.get(_stale_key(cache_id))
    if stale:
        return CachedProbe(models=stale, is_stale=True, error=result.error)
    return CachedProbe(models=[], is_stale=False, error=result.error)


def invalidate(key_id) -> None:
    cache_id = str(key_id)
    cache.delete(_fresh_key(cache_id))
    cache.delete(_stale_key(cache_id))
