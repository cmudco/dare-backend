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
It pulls the bot's billing config from SocraticBooks (cached) and applies a
single rule: the chatter pays from their active wallet; if the chatter is
anonymous (public bot, no authenticated user) the bot owner's active wallet
pays. Anonymous public traffic is also guarded by the bot's deployment cap.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Any, Dict

from api_keys.models import UserProviderAPIKey
from billing.constants import UserWalletPreferenceTypeChoice
from billing.exceptions import PaymentRequiredError
from billing.models import (
    LiteLLMKey,
    UserWalletPreference,
)
from feature_flags.services import is_flag_enabled_for_user
from users.models import User

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
    if not is_flag_enabled_for_user(user, "enable_byok"):
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
    if not is_flag_enabled_for_user(user, "enable_litellm_wallet"):
        # Mirror the BYO disabled path: reset preference and fall back to DARE
        # so a flipped-off flag doesn't leave users stuck with a wallet they
        # can no longer route through.
        pref.reset_to_dare()
        return ResolvedWallet(type=UserWalletPreferenceTypeChoice.DARE)
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
# bot resolution surface independent of the user's wallet preference.
BOT_WALLET_DARE = 'DARE'
BOT_WALLET_BYO = 'BYO'
BOT_WALLET_LITELLM = 'LITELLM'


# Fallback reason codes — written to ``Transaction.fallback_reason`` when we
# can't resolve the bot owner / config. Read by audits / dashboards to explain
# why DARE billed without an attributable owner.
FALLBACK_BOT_CONFIG_UNAVAILABLE = 'BOT_CONFIG_UNAVAILABLE'
FALLBACK_OWNER_NOT_FOUND = 'OWNER_NOT_FOUND'


@dataclass
class ResolvedBotWallet:
    """Outcome of `resolve_active_wallet_for_bot`.

    Attributes:
        type: One of ``BOT_WALLET_*`` constants — what to do with the cost.
        payer_user: The DARE user whose wallet/key actually pays.
        bot_owner: The bot creator (DARE user). Always set when known so the
            owner usage dashboard can aggregate ``Transaction.bot_owner=...``.
        litellm_key: The key to route through when ``type=LITELLM``.
        is_external: True for BYO/LITELLM (zero-amount Transaction; cost paid
            externally). False for DARE (real wallet movement).
        fallback_reason: Discriminated string when we couldn't resolve the
            owner cleanly; ``None`` on the happy path. Persisted on the
            resulting Transaction row.
    """
    type: str
    payer_user: Optional[Any] = None
    bot_owner: Optional[Any] = None
    litellm_key: Optional[LiteLLMKey] = None
    is_external: bool = False
    fallback_reason: Optional[str] = None


def _bot_owner_user(owner_dare_user_id: Optional[int]):
    """Look up the bot owner's DARE user, or None on missing/zero id."""
    if not owner_dare_user_id:
        return None
    return User.objects.filter(pk=owner_dare_user_id).first()


def _resolve_payer(payer, owner, *, requested_provider: Optional[str]) -> ResolvedBotWallet:
    """Resolve ``payer``'s active wallet (DARE / BYO / LiteLLM) into a
    ``ResolvedBotWallet``. ``owner`` is preserved for attribution regardless
    of who actually pays."""
    inner = resolve_active_wallet(payer, requested_provider=requested_provider)
    if inner.type == UserWalletPreferenceTypeChoice.BYO:
        return ResolvedBotWallet(
            type=BOT_WALLET_BYO,
            payer_user=payer,
            bot_owner=owner,
            is_external=True,
        )
    if inner.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = None
        if inner.ref_id is not None:
            key = LiteLLMKey.objects.filter(pk=inner.ref_id).first()
        return ResolvedBotWallet(
            type=BOT_WALLET_LITELLM,
            payer_user=payer,
            bot_owner=owner,
            litellm_key=key,
            is_external=True,
        )
    return ResolvedBotWallet(
        type=BOT_WALLET_DARE,
        payer_user=payer,
        bot_owner=owner,
        is_external=False,
    )


def resolve_active_wallet_for_bot(
    bot_id: int,
    calling_user=None,
    conversation=None,
    *,
    requested_provider: Optional[str] = None,
) -> Optional[ResolvedBotWallet]:
    """Resolve the wallet that should pay for an LLM call inside a bot conversation.

    Rule: the chatter pays from their active wallet; if the chatter is
    anonymous (public bot, no authenticated user), the bot owner's active
    wallet pays instead.

    Args:
        bot_id: SocraticBooks Bot ID.
        calling_user: Authenticated DARE user, or ``None`` for anonymous
            public-bot calls.
        conversation: Unused. Kept for call-site compatibility.
        requested_provider: Forwarded to the BYO branch of the per-user router.

    Returns ``None`` only when the bot's billing config can't be fetched at
    all (SB unreachable, bot deleted). Bot callers should fail clearly instead
    of falling back to non-bot billing.
    """
    del conversation  # unused since per-bot billing-source/access-code routing was removed
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

    is_authed = bool(calling_user) and getattr(calling_user, 'is_authenticated', False)
    if not is_authed and config.is_publicly_deployed:
        if config.budget is None or config.budget <= 0:
            raise PaymentRequiredError(
                'Public bot is missing a deployment budget cap',
                code='BOT_CAP_REACHED',
                details={'bot_id': bot_id},
            )
        if config.budget_used >= config.budget:
            raise PaymentRequiredError(
                'Public bot has reached its deployment budget cap',
                code='BOT_CAP_REACHED',
                details={
                    'bot_id': bot_id,
                    'budget': str(config.budget),
                    'budget_used': str(config.budget_used),
                },
            )

    payer = calling_user if is_authed else owner
    return _resolve_payer(payer, owner, requested_provider=requested_provider)
