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

Supports both sync and async contexts via sync_to_async wrapper.
"""

import logging
from typing import Optional

from asgiref.sync import sync_to_async

from billing.constants import UserWalletPreferenceTypeChoice
from billing.wallet_router import resolve_active_wallet
from config import env
from conversations.constants import Provider
from conversations.models import ProviderAPIKey
from core.services.dtos import ResolvedDispatchCredentials

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
    """
    if provider == Provider.LLAMA.value:
        logger.debug(f"Provider '{provider}' is local (Ollama), no API key required")
        return ResolvedDispatchCredentials(
            api_key=None,
            wallet_type=UserWalletPreferenceTypeChoice.DARE,
        )

    wallet = resolve_active_wallet(user, requested_provider=provider)

    if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
        return ResolvedDispatchCredentials(
            api_key=wallet.credentials["api_key"],
            base_url=wallet.credentials.get("base_url"),
            wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
        )

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


def user_has_provider_api_key(provider: str, user) -> bool:
    """Check if a user can authorize a call to this provider via their active wallet."""
    try:
        get_dispatch_credentials_for_user_sync(provider, user)
        return True
    except ValueError:
        return False
