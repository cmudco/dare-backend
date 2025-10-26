"""
API Key Resolution Service

Provides centralized access to provider API keys with fallback mechanism:
1. For users in OWN_API billing mode: use their UserProviderAPIKey
2. For users in WALLET billing mode: use admin's ProviderAPIKey
3. Fallback to environment variables if needed
4. Raise error if no key found anywhere

This allows both user-provided keys and platform keys to coexist.

Supports both sync and async contexts via sync_to_async wrapper.
"""

import logging
from typing import Optional
from asgiref.sync import sync_to_async

from config import env
from conversations.constants import Provider
from conversations.models import ProviderAPIKey
from api_keys.models import UserProviderAPIKey
from api_keys.constants import BillingModeChoice

logger = logging.getLogger(__name__)


def get_provider_api_key_sync(provider: str) -> str:
    """
    Synchronous version: Get API key for a provider with database-first, env-fallback strategy.

    Resolution order:
    1. Check ProviderAPIKey table for active key
    2. Fallback to environment variable
    3. Raise ValueError if no key found

    Args:
        provider: Provider identifier (e.g., 'openai', 'claude', 'gemini', 'llama')

    Returns:
        API key string

    Raises:
        ValueError: If no API key found in database or environment

    Example:
        >>> api_key = get_provider_api_key_sync(Provider.OPENAI.value)
        >>> client = OpenAI(api_key=api_key)
    """
    # Step 1: Try database first (active keys only)
    try:
        provider_key = ProviderAPIKey.active_objects.get(provider=provider)
        logger.info(f"Using database API key for provider: {provider}")
        return provider_key.api_key
    except ProviderAPIKey.DoesNotExist:
        logger.debug(f"No database API key found for provider: {provider}, falling back to environment")

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


# Async-safe version using sync_to_async
async def get_provider_api_key(provider: str) -> str:
    """
    Async version: Get API key for a provider with database-first, env-fallback strategy.

    This is the async-safe version that can be called from async contexts (WebSockets, async views).
    It wraps the synchronous database query with sync_to_async.

    Args:
        provider: Provider identifier (e.g., 'openai', 'claude', 'gemini', 'llama')

    Returns:
        API key string

    Raises:
        ValueError: If no API key found in database or environment

    Example:
        >>> api_key = await get_provider_api_key(Provider.OPENAI.value)
        >>> client = AsyncOpenAI(api_key=api_key)
    """
    return await sync_to_async(get_provider_api_key_sync)(provider)


def _get_env_api_key(provider: str) -> Optional[str]:
    """
    Get API key from environment variables based on provider.

    Args:
        provider: Provider identifier

    Returns:
        API key from environment or None if not set
    """
    env_key_map = {
        Provider.OPENAI.value: getattr(env, 'OPENAI_API_KEY', None),
        Provider.CLAUDE.value: getattr(env, 'CLAUDE_API_KEY', None),
        Provider.GEMINI.value: getattr(env, 'GEMINI_API_KEY', None),
        Provider.LLAMA.value: None,  # Ollama/LLaMA is local, no API key needed
    }

    return env_key_map.get(provider)


def has_provider_api_key(provider: str) -> bool:
    """
    Check if an API key exists for a provider (database or environment).

    Args:
        provider: Provider identifier

    Returns:
        True if API key exists, False otherwise
    """
    try:
        get_provider_api_key(provider)
        return True
    except ValueError:
        return False


def get_all_configured_providers() -> list:
    """
    Get list of all providers that have API keys configured.

    Returns:
        List of provider identifiers that have keys available
    """
    configured = []

    for provider in Provider:
        if has_provider_api_key(provider.value):
            configured.append(provider.value)

    return configured


# ===== NEW: User-Specific API Key Resolution =====

def get_provider_api_key_for_user_sync(provider: str, user) -> str:
    """
    Get API key for a provider based on user's billing mode.

    Resolution logic:
    1. If user.billing_mode == OWN_API: Use user's UserProviderAPIKey
    2. If user.billing_mode == WALLET: Use admin's ProviderAPIKey (system key)
    3. Fallback to environment variables
    4. Raise error if no key found

    Args:
        provider: Provider identifier (e.g., 'openai', 'claude', 'gemini', 'llama')
        user: User instance

    Returns:
        API key string

    Raises:
        ValueError: If no appropriate API key is found

    Example:
        >>> api_key = get_provider_api_key_for_user_sync(Provider.OPENAI.value, request.user)
        >>> client = OpenAI(api_key=api_key)
    """
    # Check user's billing mode
    if user.billing_mode == BillingModeChoice.OWN_API:
        # User wants to use their own API key
        try:
            user_key = UserProviderAPIKey.active_objects.get(
                user=user,
                provider=provider
            )
            if user_key.api_key:
                logger.info(f"Using user's own API key for provider: {provider} (user: {user.email})")
                return user_key.api_key
            else:
                raise ValueError(
                    f"User {user.email} is in OWN_API mode but has no API key set for provider '{provider}'. "
                    f"Please add an API key in settings or switch to WALLET mode."
                )
        except UserProviderAPIKey.DoesNotExist:
            raise ValueError(
                f"User {user.email} is in OWN_API mode but has no API key record for provider '{provider}'."
            )

    # WALLET mode: Use admin's system key (existing logic)
    logger.info(f"Using system API key for provider: {provider} (user: {user.email} in WALLET mode)")
    return get_provider_api_key_sync(provider)


async def get_provider_api_key_for_user(provider: str, user) -> str:
    """
    Async version: Get API key for a provider based on user's billing mode.

    This is the async-safe version that can be called from async contexts (WebSockets, async views).

    Args:
        provider: Provider identifier (e.g., 'openai', 'claude', 'gemini', 'llama')
        user: User instance

    Returns:
        API key string

    Raises:
        ValueError: If no appropriate API key is found

    Example:
        >>> api_key = await get_provider_api_key_for_user(Provider.OPENAI.value, request.user)
        >>> client = AsyncOpenAI(api_key=api_key)
    """
    return await sync_to_async(get_provider_api_key_for_user_sync)(provider, user)


def user_has_provider_api_key(provider: str, user) -> bool:
    """
    Check if a user has access to an API key for a specific provider.

    Considers both user's own keys (OWN_API mode) and system keys (WALLET mode).

    Args:
        provider: Provider identifier
        user: User instance

    Returns:
        True if user can access an API key for this provider, False otherwise
    """
    try:
        get_provider_api_key_for_user_sync(provider, user)
        return True
    except ValueError:
        return False
