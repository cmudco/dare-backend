from enum import Enum
from django.db import models

APP_NAME = "conversations"

class SenderType(models.IntegerChoices):
    PLAYER = 1, "Player"
    AI_ASSISTANT = 2, "AI Assistant"

class Provider(Enum):
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    LLAMA = "llama"
    CUSTOM = "custom"

    @classmethod
    def choices(cls):
        return [(provider.value, provider.name.replace("_", " ").title()) for provider in cls]

class ModelTier(models.TextChoices):
    PREMIUM = 'premium', 'Premium'
    STANDARD = 'standard', 'Standard'
    ECONOMY = 'economy', 'Economy'

class FeedbackType(models.TextChoices):
    LIKE = 'like', 'Like'
    DISLIKE = 'dislike', 'Dislike'

class ConversationSource(models.TextChoices):
    DARE = 'DARE', 'DARE'
    SOCRATIC_BOTS = 'SocraticBots', 'SocraticBots'

class WebSocketMessageType(Enum):
    """WebSocket message types for outgoing messages."""
    MESSAGE = "message"
    AI_STREAM = "ai_stream"
    ERROR = "error"
    PROGRESS_STREAM = "progress_stream"
    PROGRESS_COMPLETE = "progress_complete"
    CONVERSATION_HISTORY = "conversation_history"
    LATEST_PROGRESS = "latest_progress"
    # Artifact-related message types
    ARTIFACT_INIT = "artifact_init"
    ARTIFACT_STREAM = "artifact_stream"
    ARTIFACT_PAUSE = "artifact_pause"
    ARTIFACT_COMPLETE = "artifact_complete"
    ARTIFACT_MODIFY_INIT = "artifact_modify_init"  # Modification started (append sections)
    ARTIFACT_CREATED = "artifact_created"  # Tool-created artifact (chart, diagram)

class WebSocketAction(Enum):
    """WebSocket actions for incoming messages."""
    NEW_MESSAGE = "new_message"
    EDIT_MESSAGE = "edit_message"
    REGENERATE_RESPONSE = "regenerate_response"
    LOAD_HISTORY = "load_history"
    CONTINUE_ARTIFACT = "continue_artifact"


class ArtifactType(models.TextChoices):
    """Types of artifacts that can be generated."""
    DOCUMENT = 'document', 'Document'
    CODE = 'code', 'Code'
    DIAGRAM = 'diagram', 'Diagram'
    CHART = 'chart', 'Chart'
    REACT = 'react', 'React Component'


class ArtifactStatus(models.TextChoices):
    """Status of artifact generation."""
    PLANNING = 'planning', 'Planning'
    GENERATING = 'generating', 'Generating'
    PAUSED = 'paused', 'Paused'
    COMPLETED = 'completed', 'Completed'
    ERROR = 'error', 'Error'


class ArtifactAction(models.TextChoices):
    """
    Action type for artifact generation/modification.
    Used by frontend to indicate user intent.
    """
    AUTO = 'auto', 'Auto Detect'
    CREATE = 'create', 'Create New'
    MODIFY = 'modify', 'Modify Existing'


class ToolCallStatus(models.TextChoices):
    """Status of MCP tool call execution."""
    PENDING = 'pending', 'Pending'
    AWAITING_CONFIRMATION = 'awaiting_confirmation', 'Awaiting Confirmation'
    EXECUTING = 'executing', 'Executing'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'

# Default message sender names
DEFAULT_AI_SENDER_NAME = "AI Assistant"
DEFAULT_ANONYMOUS_USER_NAME = "Anonymous User"
DEFAULT_CONVERSATION_TITLE = "New Chat"

# Default LLM configuration values
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 8000
DEFAULT_MAX_CONTEXT_SNIPPETS = 4
DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD = 0.5
DEFAULT_HISTORY_LIMIT = 20

# Default learning progress tracking prompt (for Socratic platform)
DEFAULT_TRACKING_PROMPT = """You are an AI tutor designed to assess student learning progress. Based on the conversation history and learning goals provided, evaluate the student's understanding and provide constructive feedback.

Please analyze:
1. What concepts the student has grasped well
2. Areas where they need improvement
3. Specific misconceptions or gaps in understanding
4. Recommendations for next steps in their learning journey

Provide your assessment in a clear, encouraging format that helps track their progress toward the learning goals."""

# Artifact configuration defaults
DEFAULT_ARTIFACT_SECTIONS_PER_ITERATION = 3
DEFAULT_ARTIFACT_MAX_ITERATIONS = 5


# Error codes and messages
class ErrorCode:
    """Standard error codes for WebSocket responses."""
    # JSON/Data errors
    INVALID_JSON = "invalid_json"
    MISSING_DATA = "missing_data"
    VALIDATION_ERROR = "validation_error"

    # Conversation/Message errors
    INVALID_CONVERSATION = "invalid_conversation"
    INVALID_MESSAGE = "invalid_message"
    INVALID_EDIT = "invalid_edit"
    NO_USER_MESSAGE = "no_user_message"

    # Billing/Credit errors
    INSUFFICIENT_CREDITS = "insufficient_credits"
    INSUFFICIENT_BALANCE = "insufficient_balance"

    # Processing errors
    PROCESSING_ERROR = "processing_error"
    AI_RESPONSE_ERROR = "ai_response_error"
    STREAM_ERROR = "stream_error"
    REGENERATE_ERROR = "regenerate_error"
    EDIT_ERROR = "edit_error"
    FINALIZE_ERROR = "finalize_error"

    # Artifact errors
    ARTIFACT_ERROR = "artifact_error"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    ARTIFACT_ALREADY_COMPLETE = "artifact_already_complete"

class ErrorMessage:
    """Standard error messages for WebSocket responses."""
    # JSON/Data errors
    INVALID_JSON = "Invalid JSON format"
    MISSING_DATA = "Missing required data"
    MISSING_MESSAGE_ID = "Missing message_id"
    MISSING_MESSAGE_CONTENT = "Missing message_id or message content"

    # Conversation/Message errors
    INVALID_CONVERSATION = "Invalid conversation_id"
    INVALID_MESSAGE = "AI message not found"
    NO_USER_MESSAGE = "No preceding user message found"

    # Billing/Credit errors
    INSUFFICIENT_CREDITS = "Insufficient wallet balance"
    INSUFFICIENT_BALANCE = "Insufficient wallet balance"

    # Processing errors
    PROCESSING_ERROR = "Failed to process message"
    AI_RESPONSE_ERROR = "Failed to generate AI response"
    STREAM_ERROR = "Failed to stream AI response"
    REGENERATE_ERROR = "Failed to regenerate response"
    EDIT_ERROR = "Failed to edit message"
    FINALIZE_ERROR = "Failed to finalize message"

    # Artifact errors
    ARTIFACT_ERROR = "Failed to generate artifact"
    ARTIFACT_NOT_FOUND = "Artifact not found"
    ARTIFACT_ALREADY_COMPLETE = "Artifact is already complete"
    MISSING_ARTIFACT_ID = "Missing artifact_id"


# ============================================================================
# Conversation Sharing Constants
# ============================================================================

FORK_TITLE_PREFIX = "FORK OF - "
DEFAULT_FORK_TITLE = "Shared Chat"


class SharingErrorCode:
    """Error codes for conversation/workflow sharing API responses."""
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CANNOT_PUBLISH_FORKED = "cannot_publish_forked"
    FORK_FAILED = "fork_failed"


class SharingErrorMessage:
    """Error messages for conversation/workflow sharing API responses."""
    PERMISSION_DENIED = "Permission denied"
    CONVERSATION_NOT_FOUND = "Conversation not found"
    CONVERSATION_NOT_PUBLISHED = "Conversation not found or not published"
    CANNOT_PUBLISH_FORKED = "Cannot publish forked conversations. Only original conversations can be published."


# Extension-to-Renderer Mapping for unified artifact system
ARTIFACT_RENDERERS = {
    '.json': 'chart',      # application/vnd.dare.chart+json
    '.mmd': 'mermaid',     # text/mermaid
    '.md': 'markdown',
    '.py': 'code',
    '.js': 'code',
    '.ts': 'code',
    '.jsx': 'react',
    '.tsx': 'react',
    '.html': 'iframe',
    '.svg': 'svg',
}

# Content type mappings for artifacts
ARTIFACT_CONTENT_TYPES = {
    'chart': 'application/vnd.dare.chart+json',
    'diagram': 'text/mermaid',
    'document': 'text/markdown',
    'code': 'text/plain',
    'react': 'application/vnd.dare.react+jsx',
}

