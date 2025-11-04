"""
API Key Validation Services

This module provides validation services for LLM provider API keys.
Each provider has a dedicated validator that performs minimal API calls
to verify the key is valid and properly authenticated.

Best Practices Implemented:
- Minimal API calls with low cost (1-10 tokens)
- Proper exception handling for each provider
- Detailed error messages for debugging
- Timeout handling to prevent hanging requests
- Structured response format (success/failure with message)
- No sensitive data logging
"""
import logging
from typing import Tuple
from enum import Enum

import openai
import anthropic
from google import genai

from conversations.constants import Provider


logger = logging.getLogger(__name__)


class ValidationErrorCode(Enum):
    """Error codes for API key validation failures"""
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_KEY_FORMAT = "invalid_key_format"
    NETWORK_ERROR = "network_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    PERMISSION_ERROR = "permission_error"
    TIMEOUT_ERROR = "timeout_error"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_ERROR = "unknown_error"


class APIKeyValidationResult:
    """
    Structured result for API key validation.

    Attributes:
        is_valid (bool): Whether the API key is valid
        message (str): Human-readable message about the validation result
        error_code (ValidationErrorCode): Error code if validation failed
        details (dict): Additional details about the validation (e.g., model access)
        user_friendly_message (str): User-friendly error message for display
    """
    def __init__(
        self,
        is_valid: bool,
        message: str,
        error_code: ValidationErrorCode = None,
        details: dict = None,
        user_friendly_message: str = None
    ):
        self.is_valid = is_valid
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.user_friendly_message = user_friendly_message or message

    def to_dict(self):
        """Convert result to dictionary format"""
        result = {
            'is_valid': self.is_valid,
            'message': self.message,
            'user_friendly_message': self.user_friendly_message,
        }
        if self.error_code:
            result['error_code'] = self.error_code.value
        if self.details:
            result['details'] = self.details
        return result


class OpenAIKeyValidator:
    """Validator for OpenAI API keys"""

    @staticmethod
    def validate(api_key: str) -> APIKeyValidationResult:
        """
        Validate OpenAI API key by listing available models.

        This is a lightweight operation that confirms:
        1. The key is properly formatted
        2. The key is authenticated
        3. The account has access to models

        Args:
            api_key: OpenAI API key (should start with 'sk-')

        Returns:
            APIKeyValidationResult with validation status and details
        """
        if not api_key or not isinstance(api_key, str):
            return APIKeyValidationResult(
                is_valid=False,
                message="API key is required and must be a string",
                error_code=ValidationErrorCode.INVALID_KEY_FORMAT,
                user_friendly_message="Invalid API key format for openai. API key is required."
            )

        # Basic format validation
        if not api_key.startswith('sk-'):
            return APIKeyValidationResult(
                is_valid=False,
                message="OpenAI API key must start with 'sk-'",
                error_code=ValidationErrorCode.INVALID_KEY_FORMAT,
                user_friendly_message="Invalid API key format for openai. OpenAI API key must start with 'sk-'"
            )

        try:
            # Create client with the provided key
            client = openai.OpenAI(api_key=api_key, timeout=10.0)

            # List models - lightweight operation to verify authentication
            models_response = client.models.list()

            # Extract model count for details
            model_count = len(list(models_response.data)) if hasattr(models_response, 'data') else 0

            return APIKeyValidationResult(
                is_valid=True,
                message="OpenAI API key is valid and authenticated",
                details={
                    'provider': 'openai',
                    'model_access_count': model_count
                }
            )

        except openai.AuthenticationError as e:
            logger.warning(f"OpenAI authentication failed: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Invalid OpenAI API key - authentication failed",
                error_code=ValidationErrorCode.AUTHENTICATION_ERROR,
                user_friendly_message="Invalid API key for openai. Please check your key and try again."
            )

        except openai.PermissionDeniedError as e:
            logger.warning(f"OpenAI permission denied: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="OpenAI API key lacks necessary permissions",
                error_code=ValidationErrorCode.PERMISSION_ERROR,
                user_friendly_message="Your openai API key lacks necessary permissions. Please check your account settings."
            )

        except openai.RateLimitError as e:
            logger.warning(f"OpenAI rate limit hit during validation: {str(e)}")
            # Key is technically valid if we hit rate limit
            return APIKeyValidationResult(
                is_valid=True,
                message="OpenAI API key is valid (rate limit encountered during validation)",
                details={'provider': 'openai', 'rate_limited': True},
                user_friendly_message="OpenAI API key is valid (rate limit encountered during validation)"
            )

        except openai.APIConnectionError as e:
            logger.error(f"OpenAI connection error: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Unable to connect to OpenAI servers - please check your network",
                error_code=ValidationErrorCode.NETWORK_ERROR,
                user_friendly_message="Unable to connect to openai servers. Please check your internet connection and try again."
            )

        except openai.APITimeoutError as e:
            logger.error(f"OpenAI timeout: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Request to OpenAI timed out - please try again",
                error_code=ValidationErrorCode.TIMEOUT_ERROR,
                user_friendly_message="Unable to connect to openai servers. Please check your internet connection and try again."
            )

        except Exception as e:
            logger.error(f"Unexpected error validating OpenAI key: {str(e)}", exc_info=True)
            return APIKeyValidationResult(
                is_valid=False,
                message=f"Unexpected error during validation: {str(e)}",
                error_code=ValidationErrorCode.UNKNOWN_ERROR,
                user_friendly_message=f"Unexpected error validating openai API key: {str(e)}"
            )


class ClaudeKeyValidator:
    """Validator for Anthropic Claude API keys"""

    @staticmethod
    def validate(api_key: str) -> APIKeyValidationResult:
        """
        Validate Anthropic Claude API key with a minimal test request.

        Uses the messages endpoint with max_tokens=1 to minimize cost
        while confirming authentication.

        Args:
            api_key: Anthropic API key (should start with 'sk-ant-')

        Returns:
            APIKeyValidationResult with validation status and details
        """
        if not api_key or not isinstance(api_key, str):
            return APIKeyValidationResult(
                is_valid=False,
                message="API key is required and must be a string",
                error_code=ValidationErrorCode.INVALID_KEY_FORMAT,
                user_friendly_message="Invalid API key format for claude. API key is required."
            )

        # Basic format validation
        if not api_key.startswith('sk-ant-'):
            return APIKeyValidationResult(
                is_valid=False,
                message="Anthropic API key must start with 'sk-ant-'",
                error_code=ValidationErrorCode.INVALID_KEY_FORMAT,
                user_friendly_message="Invalid API key format for claude. Anthropic API key must start with 'sk-ant-'"
            )

        try:
            # Create client with the provided key
            client = anthropic.Anthropic(api_key=api_key, timeout=10.0)

            # Minimal message request (1 token output to minimize cost)
            message = client.messages.create(
                model="claude-3-5-sonnet-20241022",  # Latest stable model
                max_tokens=1,
                messages=[{"role": "user", "content": "Hi"}]
            )

            # If we get here, the key is valid
            return APIKeyValidationResult(
                is_valid=True,
                message="Anthropic Claude API key is valid and authenticated",
                details={
                    'provider': 'claude',
                    'model': message.model if hasattr(message, 'model') else None
                }
            )

        except anthropic.AuthenticationError as e:
            logger.warning(f"Claude authentication failed: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Invalid Anthropic API key - authentication failed",
                error_code=ValidationErrorCode.AUTHENTICATION_ERROR,
                user_friendly_message="Invalid API key for claude. Please check your key and try again."
            )

        except anthropic.PermissionDeniedError as e:
            logger.warning(f"Claude permission denied: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Anthropic API key lacks necessary permissions",
                error_code=ValidationErrorCode.PERMISSION_ERROR,
                user_friendly_message="Your claude API key lacks necessary permissions. Please check your account settings."
            )

        except anthropic.RateLimitError as e:
            logger.warning(f"Claude rate limit hit during validation: {str(e)}")
            # Key is technically valid if we hit rate limit
            return APIKeyValidationResult(
                is_valid=True,
                message="Anthropic Claude API key is valid (rate limit encountered during validation)",
                details={'provider': 'claude', 'rate_limited': True},
                user_friendly_message="Anthropic Claude API key is valid (rate limit encountered during validation)"
            )

        except anthropic.APIConnectionError as e:
            logger.error(f"Claude connection error: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Unable to connect to Anthropic servers - please check your network",
                error_code=ValidationErrorCode.NETWORK_ERROR,
                user_friendly_message="Unable to connect to claude servers. Please check your internet connection and try again."
            )

        except anthropic.APITimeoutError as e:
            logger.error(f"Claude timeout: {str(e)}")
            return APIKeyValidationResult(
                is_valid=False,
                message="Request to Anthropic timed out - please try again",
                error_code=ValidationErrorCode.TIMEOUT_ERROR,
                user_friendly_message="Unable to connect to claude servers. Please check your internet connection and try again."
            )

        except Exception as e:
            logger.error(f"Unexpected error validating Claude key: {str(e)}", exc_info=True)
            return APIKeyValidationResult(
                is_valid=False,
                message=f"Unexpected error during validation: {str(e)}",
                error_code=ValidationErrorCode.UNKNOWN_ERROR,
                user_friendly_message=f"Unexpected error validating claude API key: {str(e)}"
            )


class GeminiKeyValidator:
    """Validator for Google Gemini API keys"""

    @staticmethod
    def validate(api_key: str) -> APIKeyValidationResult:
        """
        Validate Google Gemini API key.

        Uses the list models endpoint to verify authentication.

        Args:
            api_key: Google AI API key

        Returns:
            APIKeyValidationResult with validation status and details
        """
        if not api_key or not isinstance(api_key, str):
            return APIKeyValidationResult(
                is_valid=False,
                message="API key is required and must be a string",
                error_code=ValidationErrorCode.INVALID_KEY_FORMAT,
                user_friendly_message="Invalid API key format for gemini. API key is required."
            )

        try:
            # Configure the client with the provided key
            client = genai.Client(api_key=api_key)

            # List available models - lightweight operation
            models = list(client.models.list())

            if not models:
                return APIKeyValidationResult(
                    is_valid=False,
                    message="Google Gemini API key authenticated but has no model access",
                    error_code=ValidationErrorCode.PERMISSION_ERROR,
                    user_friendly_message="Your gemini API key lacks necessary permissions. Please check your account settings."
                )

            return APIKeyValidationResult(
                is_valid=True,
                message="Google Gemini API key is valid and authenticated",
                details={
                    'provider': 'gemini',
                    'model_access_count': len(models)
                }
            )

        except Exception as e:
            error_str = str(e).lower()

            # Parse common error patterns
            if 'api key not valid' in error_str or 'invalid api key' in error_str or '401' in error_str:
                logger.warning(f"Gemini authentication failed: {str(e)}")
                return APIKeyValidationResult(
                    is_valid=False,
                    message="Invalid Google Gemini API key - authentication failed",
                    error_code=ValidationErrorCode.AUTHENTICATION_ERROR,
                    user_friendly_message="Invalid API key for gemini. Please check your key and try again."
                )

            elif 'permission' in error_str or '403' in error_str:
                logger.warning(f"Gemini permission denied: {str(e)}")
                return APIKeyValidationResult(
                    is_valid=False,
                    message="Google Gemini API key lacks necessary permissions",
                    error_code=ValidationErrorCode.PERMISSION_ERROR,
                    user_friendly_message="Your gemini API key lacks necessary permissions. Please check your account settings."
                )

            elif 'rate limit' in error_str or '429' in error_str:
                logger.warning(f"Gemini rate limit hit during validation: {str(e)}")
                return APIKeyValidationResult(
                    is_valid=True,
                    message="Google Gemini API key is valid (rate limit encountered during validation)",
                    details={'provider': 'gemini', 'rate_limited': True},
                    user_friendly_message="Google Gemini API key is valid (rate limit encountered during validation)"
                )

            elif 'network' in error_str or 'connection' in error_str or 'timeout' in error_str:
                logger.error(f"Gemini connection error: {str(e)}")
                return APIKeyValidationResult(
                    is_valid=False,
                    message="Unable to connect to Google servers - please check your network",
                    error_code=ValidationErrorCode.NETWORK_ERROR,
                    user_friendly_message="Unable to connect to gemini servers. Please check your internet connection and try again."
                )

            else:
                logger.error(f"Unexpected error validating Gemini key: {str(e)}", exc_info=True)
                return APIKeyValidationResult(
                    is_valid=False,
                    message=f"Unexpected error during validation: {str(e)}",
                    error_code=ValidationErrorCode.UNKNOWN_ERROR,
                    user_friendly_message=f"Unexpected error validating gemini API key: {str(e)}"
                )


class APIKeyValidationService:
    """
    Main service for validating LLM provider API keys.

    This service routes validation requests to the appropriate
    provider-specific validator and returns structured results.
    """

    # Map providers to their validators
    # Note: LLaMA/Ollama doesn't require API key validation as it runs locally
    VALIDATORS = {
        Provider.OPENAI.value: OpenAIKeyValidator,
        Provider.CLAUDE.value: ClaudeKeyValidator,
        Provider.GEMINI.value: GeminiKeyValidator,
    }

    @classmethod
    def validate_api_key(cls, provider: str, api_key: str) -> APIKeyValidationResult:
        """
        Validate an API key for a specific provider.

        Args:
            provider: Provider name (openai, claude, gemini)
            api_key: The API key to validate

        Returns:
            APIKeyValidationResult with validation status and details

        Example:
            >>> result = APIKeyValidationService.validate_api_key('openai', 'sk-...')
            >>> if result.is_valid:
            >>>     print(f"Valid! {result.message}")
            >>> else:
            >>>     print(f"Invalid: {result.message} ({result.error_code})")
        """
        # Check if this is LLaMA provider (no validation needed)
        if provider == Provider.LLAMA.value:
            logger.info("LLaMA/Ollama doesn't require API key validation (runs locally)")
            return APIKeyValidationResult(
                is_valid=True,
                message="LLaMA/Ollama doesn't require API key validation",
                details={'provider': 'llama', 'validation_skipped': True}
            )

        # Validate provider is supported
        if provider not in cls.VALIDATORS:
            logger.error(f"Unsupported provider: {provider}")
            return APIKeyValidationResult(
                is_valid=False,
                message=f"Unsupported provider: {provider}. Supported providers: {', '.join(cls.VALIDATORS.keys())}",
                error_code=ValidationErrorCode.PROVIDER_ERROR
            )

        # Get the appropriate validator
        validator_class = cls.VALIDATORS[provider]

        try:
            # Run validation
            result = validator_class.validate(api_key)

            # Log validation attempt (without exposing key)
            masked_key = cls._mask_api_key(api_key)
            if result.is_valid:
                logger.info(f"API key validation successful for {provider} (key: {masked_key})")
            else:
                logger.warning(
                    f"API key validation failed for {provider} (key: {masked_key}): "
                    f"{result.message}"
                )

            return result

        except Exception as e:
            logger.error(f"Error in validation service for {provider}: {str(e)}", exc_info=True)
            return APIKeyValidationResult(
                is_valid=False,
                message=f"Internal validation error: {str(e)}",
                error_code=ValidationErrorCode.UNKNOWN_ERROR
            )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """
        Mask an API key for logging purposes.
        Shows first 7 and last 4 characters.

        Args:
            api_key: The API key to mask

        Returns:
            Masked version of the key (e.g., "sk-proj-***xyz123")
        """
        if not api_key or not isinstance(api_key, str):
            return "***"

        if len(api_key) <= 11:
            return f"{api_key[:3]}***{api_key[-3:]}" if len(api_key) > 6 else "***"

        return f"{api_key[:7]}***{api_key[-4:]}"
