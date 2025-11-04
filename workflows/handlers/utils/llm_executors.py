"""
LLM execution utilities for workflow handlers.

This module provides standardized patterns for LLM calls with error handling,
retry logic, and response processing following LLM provider patterns.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, AsyncGenerator, Tuple

from channels.db import database_sync_to_async

from conversations.models import LLM
from .constants import LLMDefaults, RetryConfig, LogConfig
from .error_handlers import RetryHelper, WorkflowErrorHandler
from .validation_helpers import LLMConfigValidator


logger = logging.getLogger(__name__)


# ==================== LLM Configuration ====================

@dataclass
class LLMConfig:
    """
    Configuration for LLM calls.

    Encapsulates all parameters needed for LLM interaction with validation.
    """
    max_tokens: int = LLMDefaults.STEP_MAX_TOKENS
    temperature: float = LLMDefaults.STEP_TEMPERATURE
    provider: str = LLMDefaults.DEFAULT_PROVIDER
    model: str = LLMDefaults.DEFAULT_MODEL
    structured_spec: Optional[Dict] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        is_valid, errors = LLMConfigValidator.validate_llm_config(
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        if not is_valid:
            raise ValueError(f"Invalid LLM configuration: {'; '.join(errors)}")

    @classmethod
    def for_step_node(
        cls,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        structured_spec: Optional[Dict] = None
    ) -> 'LLMConfig':
        """
        Create LLM configuration for step node.

        Args:
            max_tokens: Maximum tokens (defaults to step default)
            temperature: Temperature setting (defaults to step default)
            provider: LLM provider (defaults to OpenAI)
            model: Model identifier
            structured_spec: Optional structured output specification

        Returns:
            Configured LLMConfig instance
        """
        return cls(
            max_tokens=max_tokens or LLMDefaults.STEP_MAX_TOKENS,
            temperature=temperature or LLMDefaults.STEP_TEMPERATURE,
            provider=provider or LLMDefaults.DEFAULT_PROVIDER,
            model=model or LLMDefaults.DEFAULT_MODEL,
            structured_spec=structured_spec
        )

    @classmethod
    def for_conditional_node(
        cls,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> 'LLMConfig':
        """
        Create LLM configuration for conditional node.

        Conditional nodes use lower temperature for more deterministic routing.

        Args:
            provider: LLM provider (defaults to OpenAI)
            model: Model identifier

        Returns:
            Configured LLMConfig instance
        """
        return cls(
            max_tokens=LLMDefaults.CONDITIONAL_MAX_TOKENS,
            temperature=LLMDefaults.CONDITIONAL_TEMPERATURE,
            provider=provider or LLMDefaults.DEFAULT_PROVIDER,
            model=model or LLMDefaults.DEFAULT_MODEL,
            structured_spec=None
        )


# ==================== LLM Executor ====================

class LLMExecutor:
    """
    Executor for LLM calls with standardized patterns.

    Provides streaming and non-streaming execution with error handling,
    retry logic, and response aggregation.
    """

    @staticmethod
    async def execute_with_streaming(
        llm_service,
        message: str,
        config: LLMConfig,
        correlation_id: Optional[str] = None
    ) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
        """
        Execute LLM call with streaming response.

        Args:
            llm_service: The LLM service instance
            message: The message to send to LLM
            config: LLM configuration
            correlation_id: Optional correlation ID for logging

        Yields:
            Tuples of (text_chunk, usage_data)

        Raises:
            Exception: If LLM call fails after retries
        """
        log_prefix = f"[{correlation_id}] " if correlation_id else ""
        logger.info(f"{log_prefix}Executing LLM streaming call with {config.provider}")

        start_time = time.time()

        try:
            # Prepare messages list
            messages = [{"role": "user", "content": message}]

            # Execute streaming call
            async for chunk, usage in llm_service.stream_chat_completion(
                messages=messages,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                structured_spec=config.structured_spec
            ):
                yield chunk, usage

            elapsed = time.time() - start_time
            logger.info(f"{log_prefix}LLM streaming call completed in {elapsed:.2f}s")

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"{log_prefix}LLM streaming call failed after {elapsed:.2f}s: {str(e)}",
                exc_info=True
            )
            raise

    @staticmethod
    async def execute_and_collect(
        llm_service,
        message: str,
        config: LLMConfig,
        correlation_id: Optional[str] = None
    ) -> Tuple[str, Optional[Dict]]:
        """
        Execute LLM call and collect complete response.

        Args:
            llm_service: The LLM service instance
            message: The message to send to LLM
            config: LLM configuration
            correlation_id: Optional correlation ID for logging

        Returns:
            Tuple of (complete_response, token_usage)

        Raises:
            Exception: If LLM call fails after retries
        """
        log_prefix = f"[{correlation_id}] " if correlation_id else ""
        logger.info(f"{log_prefix}Executing LLM call with collection")

        response_text = ""
        token_usage = None

        async for chunk, usage in LLMExecutor.execute_with_streaming(
            llm_service, message, config, correlation_id
        ):
            response_text += chunk
            if usage:
                token_usage = usage

        logger.debug(
            f"{log_prefix}Collected response length: {len(response_text)} chars"
        )

        return response_text, token_usage

    @staticmethod
    async def execute_with_retry(
        llm_service,
        message: str,
        config: LLMConfig,
        max_retries: int = RetryConfig.MAX_RETRIES,
        correlation_id: Optional[str] = None
    ) -> Tuple[str, Optional[Dict]]:
        """
        Execute LLM call with automatic retry on transient failures.

        Args:
            llm_service: The LLM service instance
            message: The message to send to LLM
            config: LLM configuration
            max_retries: Maximum number of retry attempts
            correlation_id: Optional correlation ID for logging

        Returns:
            Tuple of (complete_response, token_usage)

        Raises:
            Exception: If LLM call fails after all retries
        """
        log_prefix = f"[{correlation_id}] " if correlation_id else ""
        backoff = RetryConfig.INITIAL_BACKOFF_SECONDS

        for attempt in range(max_retries + 1):
            try:
                return await LLMExecutor.execute_and_collect(
                    llm_service, message, config, correlation_id
                )

            except Exception as e:
                if attempt == max_retries:
                    logger.error(
                        f"{log_prefix}LLM call failed after {max_retries} retries"
                    )
                    raise

                # Check if error is retriable
                if not RetryHelper.is_retriable_error(e):
                    logger.warning(
                        f"{log_prefix}Non-retriable error encountered, failing immediately"
                    )
                    raise

                # Log retry attempt
                logger.warning(
                    f"{log_prefix}LLM call failed (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {backoff}s: {str(e)}"
                )

                # Wait with exponential backoff
                await asyncio.sleep(backoff)
                backoff = min(
                    backoff * RetryConfig.BACKOFF_MULTIPLIER,
                    RetryConfig.MAX_BACKOFF_SECONDS
                )


# ==================== Response Aggregator ====================

class ResponseAggregator:
    """
    Aggregator for streaming LLM responses.

    Follows pattern from LLM provider stream aggregators.
    """

    @staticmethod
    async def aggregate_stream(
        stream: AsyncGenerator[Tuple[str, Optional[Dict]], None]
    ) -> Tuple[str, Optional[Dict]]:
        """
        Aggregate all chunks from a stream into complete response.

        Args:
            stream: AsyncGenerator yielding (text_chunk, usage_data) tuples

        Returns:
            Tuple of (complete_text, final_usage_data)
        """
        response_text = ""
        token_usage = None

        async for chunk, usage in stream:
            response_text += chunk
            if usage:
                token_usage = usage

        return response_text, token_usage


# ==================== LLM Selection Helper ====================

class LLMSelector:
    """
    Helper for selecting LLM with fallback logic.

    Note: This utility helps select the LLM model but does not create
    the service instance. Handlers should use their inherited llm_service
    from BaseExecutionHandler for actual LLM calls.
    """

    @staticmethod
    async def get_llm_for_node(
        node_data,
        fallback_provider: str = LLMDefaults.DEFAULT_PROVIDER
    ):
        """
        Get LLM for a workflow node with fallback logic.

        Args:
            node_data: The node data object (StepNodeData or ConditionalNodeData)
            fallback_provider: Fallback provider if none configured

        Returns:
            LLM instance

        Raises:
            ValueError: If no LLM can be determined
        """
        # Try to get LLM from node data
        llm = await database_sync_to_async(lambda: node_data.llm)()

        if llm:
            logger.debug(f"Using configured LLM: {llm.identifier}")
            return llm

        # Fallback to default provider
        logger.warning(
            f"No LLM configured for node, falling back to {fallback_provider}"
        )

        default_llm = await database_sync_to_async(
            lambda: LLM.objects.filter(provider=fallback_provider).first()
        )()

        if not default_llm:
            raise ValueError(
                f"No LLM configured and no {fallback_provider} LLM available"
            )

        return default_llm


# ==================== Export All ====================

__all__ = [
    "LLMConfig",
    "LLMExecutor",
    "ResponseAggregator",
    "LLMSelector",
]
