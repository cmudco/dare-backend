"""
Message Validation Service

Handles validation and parsing of incoming WebSocket messages.
Provides type-safe extraction of message data with defaults.
"""

from typing import Dict, Any, List, Optional
from conversations.constants import SenderType


class MessageValidationService:
    """Service for validating and parsing WebSocket message data."""

    # Default configuration values
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 2048
    DEFAULT_MAX_CONTEXT_SNIPPETS = 10
    DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD = 0.7
    DEFAULT_HISTORY_LIMIT = 10

    @classmethod
    def validate_and_parse(
        cls, data: Dict[str, Any], default_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate and extract message data from WebSocket payload.

        Args:
            data: Raw message data from WebSocket
            default_message: Default message text if not provided

        Returns:
            Dictionary with validated and typed message data
        """
        return {
            # Core message fields
            "message": (data.get("message", default_message or "").strip()),
            "sender_type": data.get("sender_type", SenderType.PLAYER),
            "message_id": data.get("message_id"),  # For regeneration
            # Context and files
            "file_ids": cls._get_list(data, "file_ids"),
            "embedding_ids": cls._get_list(data, "embedding_ids"),
            "media_ids": cls._get_list(data, "media_ids"),
            "tag_ids": cls._get_list(data, "tag_ids"),
            "folder_ids": cls._get_list(data, "folder_ids"),
            "library_ids": cls._get_list(data, "library_ids"),
            "referenced_conversation_ids": cls._get_list(
                data, "referenced_conversation_ids"
            ),
            "referenced_conversation_history_limit": data.get(
                "referenced_conversation_history_limit", 10
            ),
            "referenced_summary_ids": cls._get_list(data, "referenced_summary_ids"),
            # Opaque dispatch id from the FE — either a stringified LLM PK or
            # ``litellm:<key_pk>:<model_name>``. Inverted via ``parse_model_id``.
            "model_id": data.get("model_id"),
            "file_owner_id": data.get(
                "file_owner_id"
            ),  # Bot creator's ID for shared access
            "prompt_id": data.get("prompt_id"),
            "temperature": data.get("temperature", cls.DEFAULT_TEMPERATURE),
            "effort": data.get("effort"),
            "max_tokens": data.get("max_tokens", cls.DEFAULT_MAX_TOKENS),
            # Document retrieval settings
            "max_context_snippets": data.get(
                "max_context_snippets", cls.DEFAULT_MAX_CONTEXT_SNIPPETS
            ),
            "document_similarity_threshold": data.get(
                "document_similarity_threshold",
                cls.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD,
            ),
            "history_limit": data.get("history_limit", cls.DEFAULT_HISTORY_LIMIT),
            # Feature flags
            "web_search_enabled": data.get("web_search_enabled"),
            "web_fetch_enabled": data.get("web_fetch_enabled"),
            "image_generation_enabled": data.get("image_generation_enabled"),
            "image_generation_settings": data.get("image_generation_settings"),
            "audio_transcription_enabled": data.get("audio_transcription_enabled"),
            "audio_transcription_settings": data.get("audio_transcription_settings"),
            "artifacts_enabled": data.get("artifacts_enabled", False),
            "artifact_id": data.get("artifact_id"),  # For continuing existing artifact
            # Artifact modification fields
            "artifact_action": data.get(
                "artifact_action", "auto"
            ),  # "auto" | "create" | "modify"
            "active_artifact_id": data.get(
                "active_artifact_id"
            ),  # Currently open artifact (for auto-detection)
            "target_artifact_id": data.get(
                "target_artifact_id"
            ),  # Explicit target override
            # Vision support (base64 encoded images)
            "images": cls._get_list(data, "images"),
            # Socratic learning features
            "enable_progress": data.get("enable_progress"),
            "tracking_prompt": data.get("tracking_prompt", ""),
            "learning_goals": data.get("learning_goals", ""),
            "progress_llm_id": data.get("progress_llm_id"),
            "bot_meta": data.get("bot_meta", {}),
            # Advanced mode (support both snake_case and camelCase)
            "is_advanced": data.get("is_advanced", data.get("isAdvanced")),
            # MCP servers for tool calls
            "mcp_server_ids": cls._get_list(data, "mcp_server_ids"),
            # DARE tools for internal tool calls
            "dare_tool_slugs": cls._get_list(data, "dare_tool_slugs"),
            # Whether to search user's memory store for context
            "use_memory": bool(data.get("use_memory", False)),
        }

    @staticmethod
    def _get_list(data: Dict[str, Any], key: str) -> List:
        """
        Safely extract a list from data dictionary.

        Args:
            data: Source dictionary
            key: Key to extract

        Returns:
            List value or empty list if not present or invalid
        """
        value = data.get(key, [])
        return value if isinstance(value, list) else []

    @classmethod
    def validate_required_fields(
        cls, data: Dict[str, Any], required_fields: List[str]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate that required fields are present and non-empty.

        Args:
            data: Validated message data
            required_fields: List of required field names

        Returns:
            Tuple of (is_valid, error_message)
        """
        for field in required_fields:
            value = data.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                return False, f"Missing required field: {field}"
        return True, None

    @classmethod
    def extract_llm_query_params(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract parameters specifically needed for LLM query.

        Args:
            data: Validated message data

        Returns:
            Dictionary of LLM query parameters
        """
        return {
            "temperature": data.get("temperature", cls.DEFAULT_TEMPERATURE),
            "effort": data.get("effort"),
            "max_tokens": data.get("max_tokens", cls.DEFAULT_MAX_TOKENS),
            "max_context_snippets": data.get(
                "max_context_snippets", cls.DEFAULT_MAX_CONTEXT_SNIPPETS
            ),
            "document_similarity_threshold": data.get(
                "document_similarity_threshold",
                cls.DEFAULT_DOCUMENT_SIMILARITY_THRESHOLD,
            ),
            "history_limit": data.get("history_limit", cls.DEFAULT_HISTORY_LIMIT),
            "web_search_enabled": data.get("web_search_enabled"),
            "web_fetch_enabled": data.get("web_fetch_enabled"),
            "image_generation_enabled": data.get("image_generation_enabled"),
            "image_generation_settings": data.get("image_generation_settings"),
            "audio_transcription_enabled": data.get("audio_transcription_enabled"),
            "audio_transcription_settings": data.get("audio_transcription_settings"),
            "artifacts_enabled": data.get("artifacts_enabled", False),
        }

    @classmethod
    def extract_context_ids(cls, data: Dict[str, Any]) -> Dict[str, List]:
        """
        Extract all context-related IDs from message data.

        Args:
            data: Validated message data

        Returns:
            Dictionary of ID lists
        """
        return {
            "file_ids": data.get("file_ids", []),
            "embedding_ids": data.get("embedding_ids", []),
            "media_ids": data.get("media_ids", []),
            "tag_ids": data.get("tag_ids", []),
            "folder_ids": data.get("folder_ids", []),
            "library_ids": data.get("library_ids", []),
            "referenced_conversation_ids": data.get("referenced_conversation_ids", []),
        }

    @classmethod
    def extract_socratic_params(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract Socratic learning-specific parameters.

        Args:
            data: Validated message data

        Returns:
            Dictionary of Socratic parameters
        """
        return {
            "enable_progress": data.get("enable_progress"),
            "tracking_prompt": data.get("tracking_prompt", ""),
            "learning_goals": data.get("learning_goals", ""),
            "progress_llm_id": data.get("progress_llm_id"),
            "bot_meta": data.get("bot_meta", {}),
            "is_advanced": data.get("is_advanced"),
        }
