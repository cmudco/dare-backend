"""
Typed client for the internal SocraticBooks billing-config endpoint.

Caching: ``get_bot_billing_config`` is hot — every LLM call into a bot
conversation runs through the wallet router. We cache for 60s using Django's
default cache. Invalidate via ``invalidate_bot_billing_config`` when the SB
side mutates a bot's owner / publish state.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotBillingConfig:
    """Snapshot of a SocraticBooks bot's billing config as DARE sees it.

    Per the new SB rule, billing always follows: chatter pays if
    authenticated; otherwise (anonymous public-bot traffic) the bot owner
    pays. There is no per-bot cap or billing-source enum any more.
    """
    bot_id: int
    owner_dare_user_id: Optional[int]
    is_publicly_deployed: bool
    is_active: bool


class SocraticBooksClient:
    """Internal SocraticBooks calls invoked from DARE during billing flows."""

    REQUEST_TIMEOUT = 5  # seconds
    BILLING_CONFIG_TTL = 60  # seconds

    @classmethod
    def _base_url(cls) -> str:
        return os.getenv('SOCRATIC_BOTS_BACKEND_URL', '').rstrip('/')

    @classmethod
    def _headers(cls) -> Optional[dict]:
        key = getattr(settings, 'DARE_INTERNAL_KEY', '')
        if not key:
            return None
        return {'X-Internal-Key': key}

    @classmethod
    def _billing_config_cache_key(cls, bot_id: int) -> str:
        return f'sb:bot:billing-config:{bot_id}'

    @classmethod
    def get_bot_billing_config(cls, bot_id: int) -> Optional[BotBillingConfig]:
        """Fetch and cache the bot's billing config.

        Returns ``None`` when the bot does not exist or the call fails — the
        wallet router treats absence as "fall back to legacy behavior" so a
        transient SB outage cannot block billing entirely.
        """
        cache_key = cls._billing_config_cache_key(bot_id)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        base = cls._base_url()
        headers = cls._headers()
        if not base or not headers:
            logger.error(
                'SocraticBooksClient unconfigured: base=%r headers_present=%s',
                base, headers is not None,
            )
            return None

        url = f'{base}/api/bots/internal/{bot_id}/billing-config/'
        try:
            response = requests.get(url, headers=headers, timeout=cls.REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.error('billing-config request failed for bot %s: %s', bot_id, exc)
            return None

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.error(
                'billing-config returned %s for bot %s: %s',
                response.status_code, bot_id, response.text[:200],
            )
            return None

        body = response.json()
        try:
            config = BotBillingConfig(
                bot_id=int(body['botId']),
                owner_dare_user_id=body.get('ownerDareUserId'),
                is_publicly_deployed=bool(body.get('isPubliclyDeployed', False)),
                is_active=bool(body.get('isActive', True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.error('billing-config payload malformed for bot %s: %s', bot_id, exc)
            return None

        cache.set(cache_key, config, cls.BILLING_CONFIG_TTL)
        return config

    @classmethod
    def invalidate_bot_billing_config(cls, bot_id: int) -> None:
        """Drop the cached config so the next call sees fresh data."""
        cache.delete(cls._billing_config_cache_key(bot_id))
