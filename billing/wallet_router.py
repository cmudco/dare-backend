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
"""
from dataclasses import dataclass
from typing import Optional, Any, Dict

from api_keys.models import UserProviderAPIKey
from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import (
    BYOKeyFeatureFlag,
    LiteLLMKey,
    UserWalletPreference,
)


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
