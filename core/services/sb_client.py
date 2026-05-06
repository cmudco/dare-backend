"""
Typed client for the small set of internal SocraticBooks endpoints DARE
consumes during the bot-billing flow.

Reasoning for living in ``core/services/`` rather than next to the conversation
helpers:
- ``BotBudgetService`` (next to conversations) is one specific call. As Phase 3
  adds two more (billing-config GET, update-cap POST) the calls cluster into a
  cohesive client.
- The cohort of endpoints shares an authentication scheme (``X-Internal-Key``),
  base URL, timeout, and cache strategy — encapsulating those once avoids
  drift.
- Future bot-billing follow-ups (refund, allocate-from-bot, voice-team budget,
  etc.) drop in here without each becoming a one-off ``requests.post``.

Caching: ``get_bot_billing_config`` is hot — every LLM call into a bot
conversation runs through the wallet router which needs the bot's billing
source/target. We cache for 60s using Django's default cache. Invalidation is
explicit via ``invalidate_bot_billing_config`` after a cap or billing-source
update propagates back from owner-facing endpoints (Phase 5).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotBillingConfig:
    """Snapshot of a bot's billing config as DARE sees it.

    Mirrors the SB internal endpoint response. Decimal fields are kept as
    Decimal here (not str) so the wallet router can compare without parsing
    on the hot path.
    """
    bot_id: int
    billing_source: str
    billing_target_id: Optional[str]
    owner_dare_user_id: Optional[int]
    budget: Optional[Decimal]
    budget_used: Decimal
    is_publicly_deployed: bool
    is_active: bool


class SocraticBooksClient:
    """Internal SocraticBooks calls invoked from DARE during billing/cap flows."""

    REQUEST_TIMEOUT = 5  # seconds
    BILLING_CONFIG_TTL = 60  # seconds; matches the plan's cache window

    @classmethod
    def _base_url(cls) -> str:
        return os.getenv('SOCRATIC_BOTS_BACKEND_URL', '').rstrip('/')

    @classmethod
    def _headers(cls) -> Optional[dict]:
        key = getattr(settings, 'DARE_INTERNAL_KEY', '')
        if not key:
            return None
        return {'X-Internal-Key': key}

    # --- Billing config ----------------------------------------------------

    @classmethod
    def _billing_config_cache_key(cls, bot_id: int) -> str:
        return f'sb:bot:billing-config:{bot_id}'

    @classmethod
    def get_bot_billing_config(cls, bot_id: int) -> Optional[BotBillingConfig]:
        """Fetch and cache the bot's billing config.

        Returns ``None`` when the bot does not exist or the call fails — the
        caller (wallet router) treats absence as "fall back to legacy
        behavior" so a transient SB outage cannot block billing entirely.
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
                billing_source=body['billingSource'],
                billing_target_id=body.get('billingTargetId'),
                owner_dare_user_id=body.get('ownerDareUserId'),
                budget=Decimal(body['budget']) if body.get('budget') is not None else None,
                budget_used=Decimal(body.get('budgetUsed') or '0'),
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
        """Drop the cached config so the next call sees fresh data.

        Called by owner-facing endpoints after a cap or billing-source update.
        """
        cache.delete(cls._billing_config_cache_key(bot_id))

    # --- Cap update --------------------------------------------------------

    @classmethod
    def update_bot_cap(cls, bot_id: int, new_cap: Decimal) -> tuple[bool, Optional[dict]]:
        """Push a new cap to SB. Returns ``(ok, body)`` — body has shape
        ``{success, budget, budget_used}`` on 200 or ``{error, message, ...}``
        on 4xx. The caller (owner endpoint) surfaces SB's error to the user
        verbatim so e.g. ``cap_below_used`` translates to a clean 400 response.
        """
        base = cls._base_url()
        headers = cls._headers()
        if not base or not headers:
            return False, {'error': 'unconfigured', 'message': 'SocraticBooks backend not configured'}

        url = f'{base}/api/bots/internal/{bot_id}/update-cap/'
        try:
            response = requests.post(
                url,
                headers=headers,
                json={'budget': str(new_cap)},
                timeout=cls.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.error('update-cap request failed for bot %s: %s', bot_id, exc)
            return False, {'error': 'sb_unreachable', 'message': str(exc)}

        body = None
        try:
            body = response.json()
        except ValueError:
            body = {'error': 'malformed_response'}

        if 200 <= response.status_code < 300:
            cls.invalidate_bot_billing_config(bot_id)
            return True, body
        return False, body
