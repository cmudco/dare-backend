"""Generation configuration DTO for LLM requests."""

from dataclasses import dataclass, replace
from typing import Optional, Dict, Any


@dataclass(frozen=True)
class GenerationConfig:
    """Configuration for LLM generation parameters.

    Controls how the LLM generates responses including temperature,
    token limits, and special features like web search.

    Attributes:
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
        max_tokens: Maximum tokens in response
        prompt_id: Custom prompt template ID
        web_search_enabled: Enable web search tool
        image_generation_enabled: Enable image generation (DALL-E)
        image_generation_settings: DALL-E settings (size, quality, style)
        structured_spec: JSON schema for structured output
    """
    temperature: float = 0.7
    max_tokens: int = 8000
    prompt_id: Optional[str] = None
    web_search_enabled: bool = False
    image_generation_enabled: bool = False
    image_generation_settings: Optional[Dict[str, Any]] = None
    structured_spec: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validate generation parameters."""
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {self.temperature}")
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")

    def is_image_generation_request(self) -> bool:
        """Check if this is an image generation request."""
        return self.image_generation_enabled

    def merge_with_conversation_defaults(
        self,
        conversation_web_search: bool = False,
        conversation_image_generation: bool = False,
    ) -> 'GenerationConfig':
        """Merge with conversation-level defaults.

        This allows conversation settings to act as defaults when request values are False.

        Args:
            conversation_web_search: Conversation-level web search setting
            conversation_image_generation: Conversation-level image generation setting

        Returns:
            New GenerationConfig with merged settings
        """
        return replace(
            self,
            web_search_enabled=self.web_search_enabled or conversation_web_search,
            image_generation_enabled=self.image_generation_enabled or conversation_image_generation,
        )
