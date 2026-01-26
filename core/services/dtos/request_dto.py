"""Main request DTO for LLM queries."""

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Optional

from .context_dto import ContextConfig
from .generation_dto import GenerationConfig
from .media_dto import MediaConfig
from .socratic_dto import SocraticConfig


@dataclass(frozen=True)
class LLMQueryRequest:
    """Main request DTO for LLM query operations.

    This replaces the 24+ parameter method signature with a single typed object.
    All parameters are organized into logical groups for better maintainability.

    Required Attributes:
        message: User's input message
        conversation: Conversation model instance (can be None for workflows)
        user: User model instance

    Optional Attributes:
        llm: LLM model to use (defaults to active LLM)
        context: Document and history context configuration
        generation: Generation parameters (temperature, tokens, etc.)
        media: Images and media files
        socratic: Socratic teaching mode configuration
        message_obj: Message model instance for tracking
        workflow_run_step_obj: Workflow step for execution tracking
    """
    # Required fields
    message: str
    user: Optional[Any] = None  # User model - using Any to avoid circular imports (can be None for public bots)

    # Semi-required (can be None for workflow execution)
    conversation: Optional[Any] = None  # Conversation model

    # Optional fields with defaults
    llm: Optional[Any] = None  # LLM model - using Any to avoid circular imports
    context: ContextConfig = field(default_factory=ContextConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    socratic: SocraticConfig = field(default_factory=SocraticConfig)
    message_obj: Optional[Any] = None  # Message model
    workflow_run_step_obj: Optional[Any] = None  # WorkflowRunStep model
    
    # MCP tool integration
    mcp_server_ids: tuple[int, ...] = field(default_factory=tuple)  # Selected MCP server IDs

    def __post_init__(self):
        """Validate request data."""
        if not self.message or not self.message.strip():
            raise ValueError("Message cannot be empty")
        # User can be None for public bot conversations
        # Validation: user is required UNLESS conversation has no user (public bot)
        if not self.user and self.conversation and hasattr(self.conversation, 'user') and self.conversation.user is not None:
            raise ValueError("User is required for authenticated conversations")

    def is_socratic_mode(self) -> bool:
        """Check if Socratic mode is enabled."""
        return self.socratic.enabled

    def is_advanced_mode(self) -> bool:
        """Check if advanced Socratic mode is enabled."""
        return self.socratic.enabled and self.socratic.advanced_mode

    def requires_web_search(self) -> bool:
        """Check if web search is enabled."""
        return self.generation.web_search_enabled

    def requires_image_generation(self) -> bool:
        """Check if image generation is enabled."""
        return self.generation.image_generation_enabled

    def requires_audio_transcription(self) -> bool:
        """Check if audio transcription is enabled."""
        return self.generation.audio_transcription_enabled

    def requires_artifact_generation(self) -> bool:
        """Check if artifact generation is enabled."""
        return self.generation.artifacts_enabled
    
    def requires_mcp_tools(self) -> bool:
        """Check if MCP tools should be loaded."""
        return len(self.mcp_server_ids) > 0

    def with_conversation_defaults(self, conversation: Any) -> 'LLMQueryRequest':
        """Apply conversation-level defaults for generation settings.

        Note: This method now simply returns self unchanged because message-level
        settings (sent from frontend) always take precedence over conversation-level
        database values. The frontend always sends the current state of toggles,
        so we trust that instead of potentially stale conversation database values.

        Args:
            conversation: Conversation model (ignored, kept for API compatibility)

        Returns:
            Self (unchanged) - message-level settings take precedence
        """
        # Message-level settings from frontend always take precedence
        # No need to merge with conversation defaults
        return self


@dataclass
class LLMQueryChunk:
    """Response chunk from LLM streaming.

    Represents a single chunk of text from the streaming LLM response
    along with optional usage statistics.

    Attributes:
        chunk: Text content of the chunk
        usage: Token usage statistics (input_tokens, output_tokens, cost)
    """
    chunk: str
    usage: Optional[dict] = None

    def has_usage(self) -> bool:
        """Check if usage data is present."""
        return self.usage is not None

    def get_input_tokens(self) -> int:
        """Get input token count."""
        return self.usage.get("input_tokens", 0) if self.usage else 0

    def get_output_tokens(self) -> int:
        """Get output token count."""
        return self.usage.get("output_tokens", 0) if self.usage else 0

    def get_cost(self) -> float:
        """Get cost in dollars."""
        cost = self.usage.get("cost", Decimal("0.00")) if self.usage else Decimal("0.00")
        return float(cost) if isinstance(cost, Decimal) else cost
