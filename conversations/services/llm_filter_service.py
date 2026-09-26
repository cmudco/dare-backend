"""
Wallet-aware filtering for the model picker.

`filter_for_active_wallet(user, base_qs)` resolves the wallet that will pay
(via `billing.wallet_router`) and returns the model list it can actually serve,
plus a `WalletMeta` block for the FE empty-state UX. `filter_for_bot(bot_id,
owner, base_qs)` lists what an owner can save on a bot, each entry tagged with
who pays for it (``paid_by``).

DARE wallets keep returning the existing access-code-group filtered catalog.
BYO wallets are filtered to the providers the user has populated keys for.
LITELLM wallets return synthetic entries from the proxy probe.

Every entry — DB-backed or LiteLLM-routed — has the same flat shape with
a string ``id``. For real LLMs the id is the stringified PK; for LiteLLM
it's ``litellm:<key_pk>:<model_name>``. The FE treats the id as opaque;
the BE inverts the encoding via ``parse_model_id`` on dispatch.

`parse_scope(raw)` parses the `?wallet_scope=` query param the LLMViewSet
forwards from `LLMViewSet.list`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from api_keys.models import UserProviderAPIKey
from billing import litellm_models_service
from billing.constants import UserWalletPreferenceTypeChoice
from billing.exceptions import BotModelUnavailable
from billing.models import LiteLLMKey
from billing.wallet_router import (
    ResolvedWallet,
    load_bot_billing,
    resolve_active_wallet,
    sponsored_litellm_key,
)
from conversations.constants import Provider
from conversations.models import LLM
from core.services.dtos.llm_descriptor_dto import (
    litellm_picker_id,
    split_litellm_picker_id,
)
from core.services.model_capabilities import family_supports_temperature
from core.services.model_identity import resolve_family

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
    """Flat picker entry for a DB-backed LLM. ``id`` is the stringified PK."""
    return {
        "id": str(model.pk),
        "name": model.name,
        "identifier": model.identifier,
        "provider": model.provider,
        "description": model.description,
        "is_active": model.is_active,
        "is_reasoning": model.is_reasoning,
        "supports_temperature": model.supports_temperature,
        "supports_effort": model.supports_effort,
        "supports_adaptive_thinking": model.supports_adaptive_thinking,
        "reasoning_level": model.reasoning_level,
        "default_effort": model.default_effort,
        "default_adaptive_thinking_enabled": model.default_adaptive_thinking_enabled,
        "is_image_generator": model.is_image_generator,
        "is_audio_transcriber": model.is_audio_transcriber,
        "input_token_rate_per_million": model.input_token_rate_per_million,
        "output_token_rate_per_million": model.output_token_rate_per_million,
        "tier": model.tier,
    }


def _litellm_entry(litellm_key, model_name: str) -> Dict[str, Any]:
    """Flat picker entry for a LiteLLM-routed model. ``id`` is opaque to the
    FE: ``litellm:<key_pk>:<model_name>``. ``parse_model_id`` on the BE
    inverts it on dispatch. Capabilities come from the resolved model family;
    image and audio flags stay false because those need provider-native
    endpoints the proxy doesn't forward. Zero rates: billing is external.
    """
    # Not ``probed.provider``: that is the upstream vendor, and gateways
    # commonly report "openai" for everything they front. This field drives
    # dispatch and credential lookup, which for a proxy model is always custom.
    provider = Provider.CUSTOM.value
    family = resolve_family(model_name)
    is_reasoning = bool(family and family.is_reasoning)
    return {
        "id": litellm_picker_id(litellm_key.pk, model_name),
        "name": model_name,
        "identifier": model_name,
        "provider": provider,
        "description": None,
        "is_active": True,
        "is_reasoning": is_reasoning,
        "reasoning_level": None,
        "supports_temperature": family_supports_temperature(family, is_reasoning),
        "supports_effort": bool(family and family.supports_effort),
        "supports_adaptive_thinking": bool(
            family and family.supports_adaptive_thinking
        ),
        "default_effort": "high",
        "default_adaptive_thinking_enabled": False,
        "is_image_generator": False,
        "is_audio_transcriber": False,
        "input_token_rate_per_million": 0,
        "output_token_rate_per_million": 0,
        "tier": None,
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
    entries = [_litellm_entry(litellm_key, m.name) for m in cached.models]
    providers = sorted({e["provider"] for e in entries})
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
    providers = sorted({e["provider"] for e in entries})
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

PAID_BY_CHATTER = "CHATTER"
PAID_BY_OWNER = "OWNER"


def filter_for_bot(
    bot_id: Optional[int],
    owner,
    base_qs,
) -> Tuple[List[Dict[str, Any]], WalletMeta]:
    """Models the owner can save on a bot, each saying who will pay for it.

    DARE catalog models are paid by each chatter from their own wallet. The
    owner's active LiteLLM key adds its models, paid by the owner. The bot's
    saved LiteLLM model stays listed while the owner can still sponsor it, so
    switching wallets never empties the form.
    """
    dare_entries, meta = _filter_for_dare(base_qs)
    entries = [_bot_entry(entry, PAID_BY_CHATTER) for entry in dare_entries]

    active = resolve_active_wallet(owner)
    if active.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = LiteLLMKey.objects.filter(pk=active.ref_id).first()
        litellm_entries, meta = _filter_for_litellm(key)
        entries += [_bot_entry(e, PAID_BY_OWNER, key) for e in litellm_entries]

    saved = _saved_sponsored_entry(bot_id, owner) if bot_id is not None else None
    if saved is not None and saved["id"] not in {e["id"] for e in entries}:
        entries.append(saved)
    # An unreachable gateway still leaves the DARE models to pick from.
    return entries, replace(meta, is_empty=not entries)


def _bot_entry(entry: Dict[str, Any], paid_by: str, key=None) -> Dict[str, Any]:
    return {
        **entry,
        "paid_by": paid_by,
        "sponsor_key_label": key.label if key is not None else None,
    }


def _saved_sponsored_entry(bot_id: int, owner) -> Optional[Dict[str, Any]]:
    config, _owner = load_bot_billing(bot_id)
    ref = config.chat_model_ref if config is not None else None
    parsed = split_litellm_picker_id(ref) if ref else None
    if parsed is None:
        return None
    try:
        key = sponsored_litellm_key(config, owner, ref)
    except BotModelUnavailable:
        return None
    model_name = parsed[1]
    return _bot_entry(_litellm_entry(key, model_name), PAID_BY_OWNER, key)


# === Bot save check ========================================================

MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
KEY_UNAVAILABLE = "KEY_UNAVAILABLE"
MODEL_NOT_ON_KEY = "MODEL_NOT_ON_KEY"
TRACKING_NEEDS_DARE_MODEL = "TRACKING_NEEDS_DARE_MODEL"


def bot_model_problem(owner, model_ref: str, *, is_tracking: bool) -> Optional[str]:
    """Why ``owner`` can't save ``model_ref`` on a bot, or ``None`` if they can.

    A DARE catalog model must be in the owner's catalog. A LiteLLM model must
    be on a key the owner can use and listed by that key's gateway. Progress
    tracking only runs on DARE catalog models.
    """
    parsed = split_litellm_picker_id(model_ref)
    if parsed is None:
        try:
            pk = int(model_ref)
        except ValueError:
            return MODEL_NOT_AVAILABLE
        visible = LLM.visible_for_user(owner).filter(pk=pk, is_active=True)
        return None if visible.exists() else MODEL_NOT_AVAILABLE
    if is_tracking:
        return TRACKING_NEEDS_DARE_MODEL

    key_id, model_name = parsed
    key = LiteLLMKey.visible_for_user(owner).filter(pk=key_id).first()
    if key is None:
        return KEY_UNAVAILABLE
    listed = {m.name for m in litellm_models_service.list_models(key).models}
    return None if model_name in listed else MODEL_NOT_ON_KEY


# === Scope parser ===========================================================


@dataclass(frozen=True)
class WalletScope:
    kind: str  # 'active' or 'bot'
    bot_id: Optional[int] = None  # None for a bot being created


def parse_scope(raw: Optional[str]) -> Optional[WalletScope]:
    """Parse `?wallet_scope=` value. Returns None for unknown / missing inputs."""
    if not raw:
        return None
    if raw == "active":
        return WalletScope(kind="active")
    if raw == "bot:new":
        return WalletScope(kind="bot")
    if raw.startswith("bot:"):
        try:
            return WalletScope(kind="bot", bot_id=int(raw.split(":", 1)[1]))
        except (ValueError, IndexError):
            return None
    return None
