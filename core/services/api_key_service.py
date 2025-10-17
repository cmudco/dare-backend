"""
API Key Resolution Service

Provides centralized access to provider API keys with fallback mechanism:
1. First, try to fetch from database (ProviderAPIKey model)
2. If not found or inactive, fallback to environment variables
3. Raise error if no key found anywhere

This allows gradual migration from env-based to database-based key management.

Supports both sync and async contexts via sync_to_async wrapper.
"""

import logging
from typing import Optional
from asgiref.sync import sync_to_async

from config import env
from conversations.constants import Provider
from conversations.models import ProviderAPIKey

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
