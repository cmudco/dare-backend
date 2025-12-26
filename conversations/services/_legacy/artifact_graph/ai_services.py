"""
AI Service Factory Functions for Artifact Generation

Provides factory functions to get the appropriate AI service
for artifact generation based on LLM provider.
"""

import logging
from typing import Tuple

from asgiref.sync import sync_to_async

from conversations.models import LLM
from conversations.constants import Provider
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.openai_service import OpenAIService
from core.services.claude_service import ClaudeService
from core.services.gemini_service import GeminiService
from core.services.llama_service import LlamaService
from core.services.custom_llm_service import CustomLLMService


logger = logging.getLogger(__name__)


async def get_ai_service(llm: LLM, user=None):
    """
    Get the appropriate AI service for an LLM.

    All services expect an LLM object and optional api_key override.
    """
    provider = llm.provider

    # Get API key (these are already async functions)
    if user:
        api_key = await get_provider_api_key_for_user(provider, user)
    else:
        api_key = await get_provider_api_key(provider)

    if not api_key:
        raise ValueError(f"No API key found for provider {provider}")

    # Return appropriate service - all take (llm, api_key) signature
    if provider == Provider.OPENAI.value:
        return OpenAIService(llm=llm, api_key=api_key)
    elif provider == Provider.CLAUDE.value:
        return ClaudeService(llm=llm, api_key=api_key)
    elif provider == Provider.GEMINI.value:
        return GeminiService(llm=llm, api_key=api_key)
    elif provider == Provider.LLAMA.value:
        return LlamaService(llm=llm, api_key=api_key)
    elif provider == Provider.CUSTOM.value:
        return CustomLLMService(llm=llm, api_key=api_key)
    else:
        # Default to OpenAI-compatible
        return OpenAIService(llm=llm, api_key=api_key)


async def get_structured_output_service(llm: LLM, user=None) -> Tuple:
    """
    Get an AI service that supports structured output for artifact planning.
    
    For providers that don't support structured output (LLaMA, Custom),
    falls back to OpenAI or Claude.
    
    Returns:
        Tuple of (ai_service, is_fallback, provider_name)
    """
    provider = llm.provider
    
    # Providers that support structured output natively
    supported_providers = [
        Provider.OPENAI.value,
        Provider.CLAUDE.value, 
        Provider.GEMINI.value,
    ]
    
    if provider in supported_providers:
        service = await get_ai_service(llm, user)
        return service, False, provider
    
    # Fall back to OpenAI for unsupported providers
    logger.info(f"Provider {provider} doesn't support structured output, falling back to OpenAI")
    
    # Get OpenAI API key
    if user:
        api_key = await get_provider_api_key_for_user(Provider.OPENAI.value, user)
    else:
        api_key = await get_provider_api_key(Provider.OPENAI.value)
    
    if not api_key:
        # Try Claude as second fallback
        logger.info("OpenAI key not found, trying Claude for structured output")
        if user:
            api_key = await get_provider_api_key_for_user(Provider.CLAUDE.value, user)
        else:
            api_key = await get_provider_api_key(Provider.CLAUDE.value)
        
        if not api_key:
            raise ValueError("No API key available for structured output (tried OpenAI and Claude)")
        
        # Get a lightweight Claude model for planning
        fallback_llm = await sync_to_async(
            LLM.objects.filter(provider=Provider.CLAUDE.value, is_active=True).first
        )()
        if not fallback_llm:
            raise ValueError("No active Claude model found for structured output fallback")
        
        return ClaudeService(llm=fallback_llm, api_key=api_key), True, Provider.CLAUDE.value
    
    # Get a lightweight OpenAI model for planning
    fallback_llm = await sync_to_async(
        LLM.objects.filter(provider=Provider.OPENAI.value, is_active=True).first
    )()
    if not fallback_llm:
        raise ValueError("No active OpenAI model found for structured output fallback")
    
    return OpenAIService(llm=fallback_llm, api_key=api_key), True, Provider.OPENAI.value
