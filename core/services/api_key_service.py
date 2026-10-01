"""
API Key Resolution Service

Provides centralized access to provider API keys with fallback mechanism:

1. **System-key path** (`get_provider_api_key[_sync]`): no user context.
   Looks up `ProviderAPIKey` table, falls back to env vars. Used for
   service-level calls where no specific user pays (e.g. health checks).

2. **User-aware dispatch path** (`get_dispatch_credentials_for_user[_sync]`):
   resolves the active wallet via `billing.wallet_router` and returns a
   `ResolvedDispatchCredentials` DTO with api_key + (optional) base_url +
   wallet_type. The dispatcher branches on `creds.use_litellm_proxy` to
   decide whether to route through a LiteLLM-compatible client.

3. **Chat-turn path** (`get_chat_dispatch_credentials[_sync]`): the model
   decides first. A LiteLLM model is sent through the key in its picker id,
   and a bot conversation asks the bot router who pays, so the key a turn is
   sent with is the key it is billed and reconciled against.

Supports both sync and async contexts via sync_to_async wrapper.
"""

import logging
from typing import Optional

from asgiref.sync import sync_to_async

from billing.constants import UserWalletPreferenceTypeChoice
from billing.exceptions import PaymentRequiredError
from billing.gateway_report import gateway_user_id
from billing.models import LiteLLMKey
from billing.services import WalletService
from billing.wallet_router import (
    BOT_WALLET_BYO,
    BOT_WALLET_LITELLM,
    ResolvedBotWallet,
    ResolvedWallet,
    litellm_key_wallet,
    resolve_active_wallet,
    resolve_active_wallet_for_bot,
)
from config import env
from conversations.constants import Provider
from conversations.models import ProviderAPIKey
from core.services.dtos import ResolvedDispatchCredentials
from core.services.dtos.llm_descriptor_dto import split_litellm_picker_id
from core.services.reference_pricing import reference_rates
from feature_flags.services import is_flag_enabled_for_user

logger = logging.getLogger(__name__)


# ===== System-key path (no user) =====


def get_provider_api_key_sync(provider: str) -> Optional[str]:
    """
    Synchronous version: Get API key for a provider with database-first, env-fallback strategy.

    Resolution order:
    1. Check if provider requires no API key (e.g., llama/Ollama is local)
    2. Check ProviderAPIKey table for active key
    3. Fallback to environment variable
    4. Raise ValueError if no key found (for providers that need one)

    Args:
        provider: Provider identifier (e.g., 'openai', 'claude', 'gemini', 'llama')

    Returns:
        API key string, or None for local providers like llama

    Raises:
        ValueError: If no API key found in database or environment (for cloud providers)
    """
    # Step 0: LLaMA/Ollama is local - no API key needed
    if provider == Provider.LLAMA.value:
        logger.debug(f"Provider '{provider}' is local (Ollama), no API key required")
        return None

    # Step 1: Try database first (active keys only)
    try:
        provider_key = ProviderAPIKey.active_objects.get(provider=provider)
        logger.info(f"Using database API key for provider: {provider}")
        return provider_key.api_key
    except ProviderAPIKey.DoesNotExist:
        logger.debug(
            f"No database API key found for provider: {provider}, falling back to environment"
        )

    # Step 2: Fallback to environment variables
    env_key = _get_env_api_key(provider)
    if env_key:
        logger.info(f"Using environment API key for provider: {provider}")
        return env_key

    # Step 3: No key found anywhere
    raise ValueError(
        f"No API key found for provider '{provider}'. "
        f"Please add a key in Django admin (Provider API Keys) or set environment variable."
    )


async def get_provider_api_key(provider: str) -> Optional[str]:
    """Async wrapper for `get_provider_api_key_sync`."""
    return await sync_to_async(get_provider_api_key_sync)(provider)


def _get_env_api_key(provider: str) -> Optional[str]:
    """Get API key from environment variables based on provider."""
    env_key_map = {
        Provider.OPENAI.value: getattr(env, "OPENAI_API_KEY", None),
        Provider.CLAUDE.value: getattr(env, "CLAUDE_API_KEY", None),
        Provider.GEMINI.value: getattr(env, "GEMINI_API_KEY", None),
        Provider.LLAMA.value: None,  # Ollama/LLaMA is local, no API key needed
    }
    return env_key_map.get(provider)


def has_provider_api_key(provider: str) -> bool:
    """Check if an API key exists for a provider (database or environment)."""
    if provider == Provider.LLAMA.value:
        return True
    try:
        get_provider_api_key_sync(provider)
        return True
    except ValueError:
        return False


def get_all_configured_providers() -> list:
    """Get list of all providers that have API keys configured."""
    configured = []
    for provider in Provider:
        if has_provider_api_key(provider.value):
            configured.append(provider.value)
    return configured


# ===== User-aware dispatch path =====


def get_dispatch_credentials_for_user_sync(
    provider: str, user
) -> ResolvedDispatchCredentials:
    """
    Resolve the credentials the user's active wallet should authorize this call with.

    Routing per `billing.wallet_router.resolve_active_wallet`:

    - LLaMA / Ollama is local — returns DARE creds with ``api_key=None``.
    - Active wallet = LITELLM → returns the proxy ``api_key`` and ``base_url``;
      ``use_litellm_proxy`` will be True.
    - Active wallet = BYO with a matching-provider key on file → returns that key.
    - Active wallet = DARE (or silent fallback) → returns the system key.

    Args:
        provider: Provider identifier (e.g. 'openai', 'claude', 'gemini', 'llama').
        user: The DARE user making the request.

    Returns:
        ResolvedDispatchCredentials carrying the api_key and routing info.

    Raises:
        ValueError: If no system key is available for a non-local provider on
            the DARE fallback path.
        PaymentRequiredError: If the user has used their spend limit on their
            group's LiteLLM key (``LITELLM_SPEND_LIMIT_REACHED``).
    """
    if provider == Provider.LLAMA.value:
        logger.debug(f"Provider '{provider}' is local (Ollama), no API key required")
        return ResolvedDispatchCredentials(
            api_key=None,
            wallet_type=UserWalletPreferenceTypeChoice.DARE,
        )

    wallet = resolve_active_wallet(user, requested_provider=provider)

    if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
        return _litellm_credentials(wallet, spender=user)

    if wallet.type == UserWalletPreferenceTypeChoice.BYO:
        return ResolvedDispatchCredentials(
            api_key=wallet.credentials["api_key"],
            wallet_type=UserWalletPreferenceTypeChoice.BYO,
        )

    return ResolvedDispatchCredentials(
        api_key=get_provider_api_key_sync(provider),
        wallet_type=UserWalletPreferenceTypeChoice.DARE,
    )


async def get_dispatch_credentials_for_user(
    provider: str, user
) -> ResolvedDispatchCredentials:
    """Async wrapper for `get_dispatch_credentials_for_user_sync`."""
    return await sync_to_async(get_dispatch_credentials_for_user_sync)(provider, user)


def get_chat_dispatch_credentials_sync(
    provider: str,
    user,
    *,
    bot_id: Optional[int] = None,
    litellm_model_ref: Optional[str] = None,
) -> ResolvedDispatchCredentials:
    """Credentials for one chat turn, decided by its model and conversation.

    Args:
        provider: Provider of the dispatched model (``custom`` for LiteLLM).
        user: The chatter, or ``None`` for anonymous public-bot traffic.
        bot_id: Set for SocraticBooks bot conversations.
        litellm_model_ref: Picker id (``litellm:<key>:<model>``) when the turn
            uses a LiteLLM model.

    Raises:
        BotModelUnavailable: A bot's LiteLLM model can't be sponsored.
        PaymentRequiredError: A spend limit or public-bot cap was reached, the
            bot's config is unavailable, or a LiteLLM key is gone.
        ValueError: No system key is configured for a DARE-paid provider.
    """
    if bot_id is not None:
        wallet = resolve_active_wallet_for_bot(
            bot_id,
            user,
            requested_provider=provider,
            litellm_model_ref=litellm_model_ref,
        )
        if wallet is None:
            raise PaymentRequiredError(
                "Unable to resolve SocraticBooks bot wallet",
                code="BOT_CONFIG_UNAVAILABLE",
                details={"bot_id": bot_id},
            )
        if user is None and wallet.type == BOT_WALLET_LITELLM:
            _require_meterable(bot_id, litellm_model_ref)
        return _bot_credentials(wallet, provider)

    if litellm_model_ref is not None:
        parsed = split_litellm_picker_id(litellm_model_ref)
        key = (
            LiteLLMKey.visible_for_user(user).filter(pk=parsed[0]).first()
            if parsed and user is not None
            else None
        )
        if key is None:
            raise PaymentRequiredError(
                "The LiteLLM key behind this model is no longer available",
                code="LITELLM_UNAVAILABLE",
            )
        return _litellm_credentials(litellm_key_wallet(key), spender=user)

    if user is None:
        return ResolvedDispatchCredentials(api_key=get_provider_api_key_sync(provider))
    return get_dispatch_credentials_for_user_sync(provider, user)


async def get_chat_dispatch_credentials(
    provider: str,
    user,
    *,
    bot_id: Optional[int] = None,
    litellm_model_ref: Optional[str] = None,
) -> ResolvedDispatchCredentials:
    """Async wrapper for `get_chat_dispatch_credentials_sync`."""
    return await sync_to_async(get_chat_dispatch_credentials_sync)(
        provider, user, bot_id=bot_id, litellm_model_ref=litellm_model_ref
    )


def _bot_credentials(
    wallet: ResolvedBotWallet, provider: str
) -> ResolvedDispatchCredentials:
    if wallet.type == BOT_WALLET_LITELLM:
        # The owner sponsors the call, so their group allowance is what it spends.
        return _litellm_credentials(
            litellm_key_wallet(wallet.litellm_key), spender=wallet.payer_user
        )
    if wallet.type == BOT_WALLET_BYO:
        return ResolvedDispatchCredentials(
            api_key=wallet.credentials["api_key"],
            wallet_type=UserWalletPreferenceTypeChoice.BYO,
        )
    return ResolvedDispatchCredentials(api_key=get_provider_api_key_sync(provider))


def _require_meterable(bot_id: int, litellm_model_ref: str) -> None:
    """Anonymous turns are held to the bot's public budget, which only a priced
    model can count against; an unpriced one would run the owner's key uncapped."""
    model_name = split_litellm_picker_id(litellm_model_ref)[1]
    if reference_rates(model_name) is None:
        raise PaymentRequiredError(
            "This bot's model can't be metered against its public budget",
            code="BOT_CAP_REACHED",
            details={"bot_id": bot_id},
        )


def _litellm_credentials(
    wallet: ResolvedWallet, *, spender
) -> ResolvedDispatchCredentials:
    if not is_flag_enabled_for_user(spender, "enable_litellm_wallet"):
        raise PaymentRequiredError(
            "LiteLLM wallets are turned off for this account",
            code="LITELLM_UNAVAILABLE",
        )
    WalletService.assert_dispatch_allowed(spender, wallet)
    return ResolvedDispatchCredentials(
        api_key=wallet.credentials["api_key"],
        base_url=wallet.credentials.get("base_url"),
        wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
        litellm_key_id=wallet.ref_id,
        gateway_user=gateway_user_id(spender),
    )


def user_has_provider_api_key(provider: str, user) -> bool:
    """Check if a user can authorize a call to this provider via their active wallet."""
    try:
        get_dispatch_credentials_for_user_sync(provider, user)
        return True
    except ValueError:
        return False
