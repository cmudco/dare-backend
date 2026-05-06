"""
Single decision point for which wallet pays for an LLM request.

`resolve_active_wallet(user, *, requested_provider=None)` is called once per
LLM dispatch (sync HTTP and async Channels streaming alike). It consults the
user's `UserWalletPreference`, validates the chosen wallet is still legitimate
(BYO flag still on, LiteLLM key not expired, group membership current), and
returns a `ResolvedWallet` describing what the dispatcher should actually use.

Self-healing semantics:
  - If the selected wallet is no longer valid (BYO disabled, LiteLLM expired /
    group exited / row missing), the preference row is rewritten to DARE.
  - If a BYO wallet is selected but the user has no key configured for the
    requested provider, we fall back to DARE for that single request *without*
    rewriting the preference (the user's BYO choice is still valid for
    matching providers).

Bot resolution: `resolve_active_wallet_for_bot(...)` is the bot-aware wrapper.
It pulls the bot's billing config from SocraticBooks (cached) and dispatches
on `Bot.billing_source`:
  - OWNER_WALLET → resolve the bot owner's preference via the per-user router
  - GROUP_WALLET → debit the AccessCodeGroup's GroupWallet (matched by
    Conversation.access_code); attribution stays on the bot owner
  - USER_WALLET → resolve the calling student's preference
  - LITELLM_KEY → route through the bot-attached LiteLLMKey (zero DARE-wallet
    impact, same as user-level LITELLM mode)
A `fallback_reason` is recorded whenever the requested billing source is
unusable (target missing, key expired, group not yet synced) so the resulting
Transaction row explains *why* DARE billed instead.
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Any, Dict

from api_keys.models import UserProviderAPIKey
from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import (
    BYOKeyFeatureFlag,
    GroupWallet,
    LiteLLMKey,
    UserWalletPreference,
)
from users.models import AccessCodeGroup, User

logger = logging.getLogger(__name__)


@dataclass
class ResolvedWallet:
    """Outcome of `resolve_active_wallet` consumed by the dispatch path."""
    type: str
    ref_id: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    group: Any = None


def _resolve_byo(pref: UserWalletPreference, user, requested_provider: Optional[str]) -> ResolvedWallet:
    """
    Collective BYO: pick the user's key for the *requested* provider. If they
    have no key for that provider, soft-fall back to DARE for this single
    request only — the BYO preference is preserved so other providers still
    route through their BYO keys.
    """
    if not BYOKeyFeatureFlag.is_byo_enabled():
        pref.reset_to_dare()
        return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)

    if not requested_provider:
        byo = (
            UserProviderAPIKey.active_objects.filter(user=user)
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .first()
        )
        if byo is None:
            pref.reset_to_dare()
            return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)
        return ResolvedWallet(
            type=UserWalletPreferenceTypeChoice.BYO,
            ref_id=None,
            credentials={"api_key": byo.api_key, "provider": byo.provider},
        )

    byo = (
        UserProviderAPIKey.active_objects.filter(user=user, provider=requested_provider)
        .exclude(api_key__isnull=True)
        .exclude(api_key="")
        .first()
    )
    if byo is None:
        return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)
    return ResolvedWallet(
        type=UserWalletPreferenceTypeChoice.BYO,
        ref_id=str(byo.pk),
        credentials={"api_key": byo.api_key, "provider": byo.provider},
    )


def _resolve_litellm(pref: UserWalletPreference, user) -> ResolvedWallet:
    key = LiteLLMKey.visible_for_user(user).filter(pk=pref.active_wallet_ref_id).first()
    if key is None:
        pref.reset_to_dare()
        return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)
    return ResolvedWallet(
        type=UserWalletPreferenceTypeChoice.LITELLM,
        ref_id=str(key.pk),
        credentials={"api_key": key.api_key, "base_url": key.base_url},
        group=key.source_group,
    )


def resolve_active_wallet(user, *, requested_provider: Optional[str] = None) -> ResolvedWallet:
    """
    Return the wallet that should pay for a single LLM request.

    Args:
        user: The authenticated user making the request.
        requested_provider: Provider being invoked (e.g. ``'openai'``,
            ``'claude'``, ``'gemini'``). Used to detect BYO provider mismatch.
            Pass ``None`` when the call is provider-agnostic (e.g. LiteLLM-routed).
    """
    pref = UserWalletPreference.get_or_create_for(user)

    if pref.active_wallet_type == UserWalletPreferenceTypeChoice.BYO:
        return _resolve_byo(pref, user, requested_provider)

    if pref.active_wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
        return _resolve_litellm(pref, user)

    return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)


# ---------------------------------------------------------------------------
# Bot-aware resolution
# ---------------------------------------------------------------------------

# Discriminated values for ``ResolvedBotWallet.type``. We use string literals
# rather than the user-facing UserWalletPreferenceTypeChoice to keep the
# bot resolution surface independent of the user's wallet preference (a
# GROUP_WALLET-funded bot still has a payer with their own preference, but
# the dispatcher only needs to know the call doesn't touch any DARE wallet).
BOT_WALLET_DARE = 'DARE'
BOT_WALLET_BYO = 'BYO'
BOT_WALLET_LITELLM = 'LITELLM'
BOT_WALLET_GROUP = 'GROUP'


# Fallback reason codes — written to ``Transaction.fallback_reason`` when the
# bot's preferred billing source can't be honored. Read by audits / dashboards
# to explain why DARE billed in place of the configured source.
FALLBACK_BOT_CONFIG_UNAVAILABLE = 'BOT_CONFIG_UNAVAILABLE'
FALLBACK_OWNER_NOT_FOUND = 'OWNER_NOT_FOUND'
FALLBACK_GROUP_TARGET_MISSING = 'GROUP_TARGET_MISSING'
FALLBACK_GROUP_WALLET_MISSING = 'GROUP_WALLET_MISSING'
FALLBACK_GROUP_INACTIVE = 'GROUP_INACTIVE'
FALLBACK_LITELLM_TARGET_MISSING = 'LITELLM_TARGET_MISSING'
FALLBACK_LITELLM_NOT_VISIBLE = 'LITELLM_NOT_VISIBLE'
FALLBACK_LITELLM_EXPIRED = 'LITELLM_EXPIRED'
FALLBACK_USER_WALLET_NO_USER = 'USER_WALLET_NO_USER'


@dataclass
class ResolvedBotWallet:
    """Outcome of `resolve_active_wallet_for_bot`.

    Attributes:
        type: One of ``BOT_WALLET_*`` constants — what to do with the cost.
        payer_user: The DARE user whose wallet/key actually pays. ``None`` for
            GROUP-funded bots (the GroupWallet is the payer; we still stamp
            ``bot_owner`` for attribution).
        bot_owner: The bot creator (DARE user). Always set when known so the
            owner usage dashboard can aggregate ``Transaction.bot_owner=...``.
        group_wallet: The GroupWallet to debit when ``type=GROUP``.
        litellm_key: The key to route through when ``type=LITELLM``.
        is_external: True for BYO/LITELLM (zero-amount Transaction; cost paid
            externally). False for DARE/GROUP (real wallet movement).
        fallback_reason: Discriminated string when we couldn't honor the
            configured ``billing_source`` and fell back to DARE; ``None`` on
            the happy path. Persisted on the resulting Transaction row.
    """
    type: str
    payer_user: Optional[Any] = None
    bot_owner: Optional[Any] = None
    group_wallet: Optional[GroupWallet] = None
    litellm_key: Optional[LiteLLMKey] = None
    is_external: bool = False
    fallback_reason: Optional[str] = None


def _bot_owner_user(owner_dare_user_id: Optional[int]):
    """Look up the bot owner's DARE user, or None on missing/zero id."""
    if not owner_dare_user_id:
        return None
    return User.objects.filter(pk=owner_dare_user_id).first()


def _fallback_to_owner_dare(
    owner,
    *,
    reason: str,
) -> ResolvedBotWallet:
    """Fall back to debiting the bot owner's DARE wallet with an audit reason."""
    return ResolvedBotWallet(
        type=BOT_WALLET_DARE,
        payer_user=owner,
        bot_owner=owner,
        is_external=False,
        fallback_reason=reason,
    )


def _resolve_owner_wallet(owner, *, requested_provider: Optional[str]) -> ResolvedBotWallet:
    """OWNER_WALLET source: route through the owner's own preference."""
    inner = resolve_active_wallet(owner, requested_provider=requested_provider)
    if inner.type == UserWalletPreferenceTypeChoice.BYO:
        return ResolvedBotWallet(
            type=BOT_WALLET_BYO,
            payer_user=owner,
            bot_owner=owner,
            is_external=True,
        )
    if inner.type == UserWalletPreferenceTypeChoice.LITELLM:
        # Look up the key by its ref_id on the inner preference.
        key = None
        if inner.ref_id is not None:
            key = LiteLLMKey.objects.filter(pk=inner.ref_id).first()
        return ResolvedBotWallet(
            type=BOT_WALLET_LITELLM,
            payer_user=owner,
            bot_owner=owner,
            litellm_key=key,
            is_external=True,
        )
    return ResolvedBotWallet(
        type=BOT_WALLET_DARE,
        payer_user=owner,
        bot_owner=owner,
        is_external=False,
    )


def _resolve_group_wallet(
    config,
    owner,
    conversation,
) -> ResolvedBotWallet:
    """GROUP_WALLET source: debit the AccessCodeGroup's GroupWallet.

    The access code is read from the conversation (denormalized at create
    time) rather than the bot's ``billing_target_id`` so a bot shared across
    multiple cohorts could in principle attribute by cohort — for now the
    code on the conversation must equal the configured target. If they
    diverge or anything's missing, fall back to the owner's DARE wallet.
    """
    target = config.billing_target_id
    if not target:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_GROUP_TARGET_MISSING)

    conv_code = getattr(conversation, 'access_code', None) if conversation else None
    code_to_match = conv_code or target

    group = (
        AccessCodeGroup.objects
        .select_related('group_wallet')
        .filter(access_code=code_to_match)
        .first()
    )
    if group is None:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_GROUP_TARGET_MISSING)
    if not group.is_active:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_GROUP_INACTIVE)

    gw = getattr(group, 'group_wallet', None) or (
        GroupWallet.objects.filter(group=group).first()
    )
    if gw is None or not gw.is_active:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_GROUP_WALLET_MISSING)

    return ResolvedBotWallet(
        type=BOT_WALLET_GROUP,
        payer_user=None,
        bot_owner=owner,
        group_wallet=gw,
        is_external=False,
    )


def _resolve_user_wallet(calling_user, owner, *, requested_provider: Optional[str]) -> ResolvedBotWallet:
    """USER_WALLET source: debit the calling user's own wallet via the per-user router."""
    if calling_user is None or not getattr(calling_user, 'is_authenticated', False):
        # Anonymous public-bot user can't have their own wallet; fall back to owner.
        return _fallback_to_owner_dare(owner, reason=FALLBACK_USER_WALLET_NO_USER)

    inner = resolve_active_wallet(calling_user, requested_provider=requested_provider)
    if inner.type == UserWalletPreferenceTypeChoice.BYO:
        return ResolvedBotWallet(
            type=BOT_WALLET_BYO,
            payer_user=calling_user,
            bot_owner=owner,
            is_external=True,
        )
    if inner.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = None
        if inner.ref_id is not None:
            key = LiteLLMKey.objects.filter(pk=inner.ref_id).first()
        return ResolvedBotWallet(
            type=BOT_WALLET_LITELLM,
            payer_user=calling_user,
            bot_owner=owner,
            litellm_key=key,
            is_external=True,
        )
    return ResolvedBotWallet(
        type=BOT_WALLET_DARE,
        payer_user=calling_user,
        bot_owner=owner,
        is_external=False,
    )


def _resolve_litellm_key(config, owner, calling_user) -> ResolvedBotWallet:
    """LITELLM_KEY source: route through the bot-attached LiteLLMKey.

    Visibility is checked against the calling user when available, and against
    the owner otherwise — admin-issued cohort keys are scoped by AccessCodeGroup
    membership, which the calling student also belongs to.
    """
    target = config.billing_target_id
    if not target:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_LITELLM_TARGET_MISSING)

    key = LiteLLMKey.objects.filter(pk=target).first()
    if key is None:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_LITELLM_TARGET_MISSING)
    if key.is_expired:
        return _fallback_to_owner_dare(owner, reason=FALLBACK_LITELLM_EXPIRED)

    visibility_user = calling_user if (calling_user and getattr(calling_user, 'is_authenticated', False)) else owner
    if visibility_user is not None:
        if not LiteLLMKey.visible_for_user(visibility_user).filter(pk=key.pk).exists():
            return _fallback_to_owner_dare(owner, reason=FALLBACK_LITELLM_NOT_VISIBLE)

    return ResolvedBotWallet(
        type=BOT_WALLET_LITELLM,
        payer_user=calling_user or owner,
        bot_owner=owner,
        litellm_key=key,
        is_external=True,
    )


def resolve_active_wallet_for_bot(
    bot_id: int,
    calling_user=None,
    conversation=None,
    *,
    requested_provider: Optional[str] = None,
) -> Optional[ResolvedBotWallet]:
    """Resolve the wallet that should pay for an LLM call inside a bot conversation.

    Args:
        bot_id: SocraticBooks Bot ID.
        calling_user: Authenticated DARE user, or ``None`` for anonymous
            public-bot calls.
        conversation: The Conversation row (used for ``access_code``). Optional
            because some pre-conversation checks may want to resolve before
            the row exists.
        requested_provider: Forwarded to the BYO branch of the per-user router.

    Returns ``None`` only when the bot's billing config can't be fetched at
    all (SB unreachable, bot deleted). The caller is expected to fall back to
    pre-bot-billing behavior in that case (legacy code path under the feature
    flag).
    """
    # Local import — sb_client lives in core/services so importing at module
    # scope here would create a billing → core import cycle on cold start.
    from core.services.sb_client import SocraticBooksClient

    config = SocraticBooksClient.get_bot_billing_config(bot_id)
    if config is None:
        return None

    owner = _bot_owner_user(config.owner_dare_user_id)
    if owner is None:
        return ResolvedBotWallet(
            type=BOT_WALLET_DARE,
            payer_user=calling_user if (calling_user and getattr(calling_user, 'is_authenticated', False)) else None,
            bot_owner=None,
            is_external=False,
            fallback_reason=FALLBACK_OWNER_NOT_FOUND,
        )

    source = config.billing_source
    if source == 'OWNER_WALLET':
        return _resolve_owner_wallet(owner, requested_provider=requested_provider)
    if source == 'GROUP_WALLET':
        return _resolve_group_wallet(config, owner, conversation)
    if source == 'USER_WALLET':
        return _resolve_user_wallet(calling_user, owner, requested_provider=requested_provider)
    if source == 'LITELLM_KEY':
        return _resolve_litellm_key(config, owner, calling_user)

    # Unknown source — fall back to owner DARE wallet with a clear marker.
    logger.warning('Unknown bot billing_source %r for bot %s; falling back to owner DARE wallet', source, bot_id)
    return _fallback_to_owner_dare(owner, reason=FALLBACK_BOT_CONFIG_UNAVAILABLE)
