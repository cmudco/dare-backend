"""
Wallet-aware filtering for the model picker.

`filter_for_active_wallet(user, base_qs)` and `filter_for_bot(bot_id, user,
base_qs)` resolve the wallet that will pay (via `billing.wallet_router`) and
return the model list it can actually serve, plus a `WalletMeta` block for
the FE empty-state UX.

DARE wallets keep returning the existing access-code-group filtered catalog.
BYO wallets are filtered to the providers the user has populated keys for.
LITELLM wallets return *synthetic* model entries from the proxy probe — they
share the LLMSerializer field shape so the FE renders both kinds with the
same component, distinguishing on `isSynthetic`.

`parse_scope(raw)` parses the `?wallet_scope=` query param the LLMViewSet
forwards from `LLMViewSet.list`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from api_keys.models import UserProviderAPIKey
from billing import litellm_models_service
from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from billing.wallet_router import (
    BOT_WALLET_BYO,
    BOT_WALLET_DARE,
    BOT_WALLET_GROUP,
    BOT_WALLET_LITELLM,
    ResolvedBotWallet,
    ResolvedWallet,
    resolve_active_wallet,
    resolve_active_wallet_for_bot,
)
from conversations.models import LLM

# === Wallet metadata wire shape ============================================
#
# Mirrors the FE WalletMeta type. `emptyReason` is null on the happy path.
# Discriminated codes (no polymorphic union — per the data-schema-contract).

EMPTY_NO_KEYS = "NO_KEYS"
EMPTY_PROBE_FAILED = "PROBE_FAILED"
EMPTY_TARGET_KEY_DELETED = "TARGET_KEY_DELETED"


@dataclass(frozen=True)
class WalletMeta:
    """Wire shape for the model-picker's wallet block.

    Capability flags (``supports_*``) tell the FE which chat toggles to
    surface for the active wallet — LiteLLM proxies don't transparently
    forward web-search / structured-output / DALL-E / Whisper requests, so
    the picker disables those toggles when ``type == LITELLM``. Tools/MCP
    are forwarded by LiteLLM in the standard OpenAI tool-call format and
    stay enabled. Discriminated boolean flags rather than a polymorphic
    blob, per rules.md §11 (separate fields for separate concerns).
    """

    type: str
    providers: List[str] = field(default_factory=list)
    is_empty: bool = False
    empty_reason: Optional[str] = None
    stale_probe: bool = False
    supports_web_search: bool = True
    supports_image_generation: bool = True
    supports_audio_transcription: bool = True
    supports_structured_output: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "providers": self.providers,
            "is_empty": self.is_empty,
            "empty_reason": self.empty_reason,
            "stale_probe": self.stale_probe,
            "supports_web_search": self.supports_web_search,
            "supports_image_generation": self.supports_image_generation,
            "supports_audio_transcription": self.supports_audio_transcription,
            "supports_structured_output": self.supports_structured_output,
        }


# === Active-scope filter ====================================================


def _llm_entry(model: LLM) -> Dict[str, Any]:
    """Picker entry for a DB-backed LLM. ``kind="llm"`` discriminator with
    the native LLM payload nested under ``llm`` — the id stays an integer
    pk, no encoding tricks (rules.md §11)."""
    return {
        "kind": "llm",
        "llm": {
            "id": model.pk,
            "name": model.name,
            "identifier": model.identifier,
            "provider": model.provider,
            "description": model.description,
            "is_reasoning": model.is_reasoning,
            "is_image_generator": model.is_image_generator,
            "is_audio_transcriber": model.is_audio_transcriber,
            "input_token_rate_per_million": model.input_token_rate_per_million,
            "output_token_rate_per_million": model.output_token_rate_per_million,
            "tier": model.tier,
        },
    }


def _litellm_entry(litellm_key, probed) -> Dict[str, Any]:
    """Picker entry for a LiteLLM-routed model. ``kind="litellm"``
    discriminator carrying the native dispatch reference fields
    (``key_id`` + ``model_name``) plus display metadata. Each field has its
    natural type — no encoded composite id."""
    return {
        "kind": "litellm",
        "litellm": {
            "key_id": str(litellm_key.pk),
            "key_label": getattr(litellm_key, "label", None),
            "model_name": probed.name,
            "provider": probed.provider or "custom",
            "name": probed.name,
        },
    }


def _filter_for_byo(user, base_qs) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    providers = list(
        UserProviderAPIKey.active_objects.filter(user=user)
        .exclude(api_key__isnull=True)
        .exclude(api_key="")
        .values_list("provider", flat=True)
        .distinct()
    )
    if not providers:
        return [], WalletMeta(
            type=UserWalletPreferenceTypeChoice.BYO,
            providers=[],
            is_empty=True,
            empty_reason=EMPTY_NO_KEYS,
        )
    qs = base_qs.filter(provider__in=providers)
    entries = [_llm_entry(m) for m in qs]
    return entries, WalletMeta(
        type=UserWalletPreferenceTypeChoice.BYO,
        providers=sorted(set(providers)),
        is_empty=not entries,
        empty_reason=EMPTY_NO_KEYS if not entries else None,
    )


def _filter_for_litellm(litellm_key) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    if litellm_key is None:
        return [], _litellm_meta(is_empty=True, empty_reason=EMPTY_TARGET_KEY_DELETED)
    cached = litellm_models_service.list_models(litellm_key)
    if not cached.models:
        return [], _litellm_meta(is_empty=True, empty_reason=EMPTY_PROBE_FAILED)
    entries = [_litellm_entry(litellm_key, m) for m in cached.models]
    providers = sorted({e["litellm"]["provider"] for e in entries})
    return entries, _litellm_meta(
        providers=providers,
        is_empty=False,
        stale_probe=cached.is_stale,
    )


def _litellm_meta(
    providers: Optional[List[str]] = None,
    is_empty: bool = False,
    empty_reason: Optional[str] = None,
    stale_probe: bool = False,
) -> WalletMeta:
    """LITELLM-scoped WalletMeta with provider-native features disabled.

    Web search, image generation, audio transcription, and structured output
    all rely on provider-native API surfaces (OpenAI Responses API, native
    Anthropic tools, DALL-E, Whisper) that the LiteLLM proxy doesn't
    transparently forward. The FE reads these flags to hide the
    corresponding chat toggles when the active wallet is LITELLM.
    """
    return WalletMeta(
        type=UserWalletPreferenceTypeChoice.LITELLM,
        providers=providers or [],
        is_empty=is_empty,
        empty_reason=empty_reason,
        stale_probe=stale_probe,
        supports_web_search=False,
        supports_image_generation=False,
        supports_audio_transcription=False,
        supports_structured_output=False,
    )


def _filter_for_dare(base_qs) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    entries = [_llm_entry(m) for m in base_qs]
    providers = sorted({e["llm"]["provider"] for e in entries})
    return entries, WalletMeta(
        type=UserWalletPreferenceTypeChoice.DARE,
        providers=providers,
        is_empty=not entries,
    )


def filter_for_active_wallet(user, base_qs) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    """Resolve the user's active wallet and filter the base catalog accordingly."""
    resolved: ResolvedWallet = resolve_active_wallet(user)

    if resolved.type == UserWalletPreferenceTypeChoice.BYO:
        return _filter_for_byo(user, base_qs)

    if resolved.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = None
        if resolved.ref_id is not None:
            key = LiteLLMKey.objects.filter(pk=resolved.ref_id).first()
        return _filter_for_litellm(key)

    return _filter_for_dare(base_qs)


# === Bot-scope filter =======================================================


def filter_for_bot(
    bot_id: int,
    calling_user,
    base_qs,
) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    """Resolve the bot's billing source and filter the catalog accordingly."""
    resolved: Optional[ResolvedBotWallet] = resolve_active_wallet_for_bot(
        bot_id, calling_user=calling_user
    )
    if resolved is None:
        # Bot config unfetchable — fall back to legacy unfiltered behavior so
        # the picker still works during SB outage.
        return _filter_for_dare(base_qs)

    if resolved.type == BOT_WALLET_LITELLM:
        return _filter_for_litellm(resolved.litellm_key)

    if resolved.type == BOT_WALLET_BYO:
        # The payer's BYO keys decide what the bot can run.
        payer = resolved.payer_user or resolved.bot_owner
        if payer is None:
            return _filter_for_dare(base_qs)
        return _filter_for_byo(payer, base_qs)

    # GROUP / DARE / fallback: cohort or owner DARE wallet pays — full catalog.
    return _filter_for_dare(base_qs)


# === Scope parser ===========================================================


@dataclass(frozen=True)
class WalletScope:
    kind: str  # 'active' or 'bot'
    bot_id: Optional[int] = None


def parse_scope(raw: Optional[str]) -> Optional[WalletScope]:
    """Parse `?wallet_scope=` value. Returns None for unknown / missing inputs."""
    if not raw:
        return None
    if raw == "active":
        return WalletScope(kind="active")
    if raw.startswith("bot:"):
        try:
            return WalletScope(kind="bot", bot_id=int(raw.split(":", 1)[1]))
        except (ValueError, IndexError):
            return None
    return None
