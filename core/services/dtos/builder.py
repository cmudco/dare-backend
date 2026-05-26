"""Builder pattern for constructing LLMQueryRequest from dictionaries."""

from typing import Dict, Any, Optional

from users.constants import AuthSourceChoice
from .request_dto import LLMQueryRequest
from .context_dto import ContextConfig
from .generation_dto import GenerationConfig
from .media_dto import MediaConfig
from .socratic_dto import SocraticConfig

# Minimum max_tokens for Socratic bots to prevent truncated responses
# This is a hotfix - the proper solution is to make this configurable per bot
SOCRATIC_MIN_MAX_TOKENS = 8000

# Artifact-creating DARE tools require large output budgets because the entire
# artifact payload (docx blocks, mermaid code, React component, etc.) is
# serialized into the tool-call input. A 2048-token cap truncates the tool call
# and silently drops the artifact.
ARTIFACT_TOOL_SLUGS = frozenset(
    {
        "create_diagram",
        "create_chart",
        "create_docx",
        "create_pptx",
        "create_react_component",
        "update_artifact",
        "update_artifact_inline",
    }
)
ARTIFACT_MIN_MAX_TOKENS = 8000


class LLMQueryRequestBuilder:
    """Builder pattern for constructing LLMQueryRequest from dictionaries.

    Useful for converting WebSocket message data or API payloads into typed DTOs.
    """

    @staticmethod
    def from_message_data(
        message: str,
        user: Any,
        message_data: Dict[str, Any],
        conversation: Optional[Any] = None,
        llm: Optional[Any] = None,
        message_obj: Optional[Any] = None,
        workflow_run_step_obj: Optional[Any] = None,
        platform: Optional[str] = None,
    ) -> LLMQueryRequest:
        """Build LLMQueryRequest from WebSocket message data.

        Args:
            message: User's message text
            user: User model instance
            message_data: Dictionary from WebSocket payload or API request
            conversation: Optional Conversation model instance
            llm: Optional LLM model instance
            message_obj: Optional Message model instance
            workflow_run_step_obj: Optional WorkflowRunStep instance
            platform: Platform name (for Socratic mode detection)

        Returns:
            Fully constructed LLMQueryRequest with conversation defaults applied
        """
        # Determine file_owner_id: prioritize message_data, fall back to conversation
        # This handles forked conversations where file_owner_id is set on the conversation
        file_owner_id = message_data.get("file_owner_id")
        if (
            not file_owner_id
            and conversation
            and hasattr(conversation, "file_owner_id")
        ):
            file_owner_id = conversation.file_owner_id

        # Build context config
        context = ContextConfig(
            file_ids=message_data.get("file_ids", []),
            embedding_ids=message_data.get("embedding_ids", []),
            file_owner_id=file_owner_id,  # For forked conversations or bot creator's ID
            media_ids=message_data.get("media_ids", []),
            tag_ids=message_data.get("tag_ids", []),
            folder_ids=message_data.get("folder_ids", []),
            referenced_conversation_ids=message_data.get(
                "referenced_conversation_ids", []
            ),
            referenced_conversation_history_limit=message_data.get(
                "referenced_conversation_history_limit", 10
            ),
            referenced_summary_ids=message_data.get("referenced_summary_ids", []),
            max_context_snippets=message_data.get("max_context_snippets", 4),
            document_similarity_threshold=message_data.get(
                "document_similarity_threshold", 0.5
            ),
            history_limit=message_data.get("history_limit", 20),
            use_memory=bool(message_data.get("use_memory", False)),
        )

        # Detect Socratic bots platform BEFORE building generation config
        is_socratic_bots = (
            platform == AuthSourceChoice.SOCRATIC_BOTS if platform else False
        )
        bot_meta = message_data.get("bot_meta", {})
        socratic_enabled = is_socratic_bots and not message_data.get("prompt_id")

        # Get max_tokens from message_data with default
        max_tokens = message_data.get("max_tokens", 8000)

        # HOTFIX: Enforce minimum max_tokens for Socratic bots to prevent truncated responses
        # Socratic bot conversations created without explicit config get conversation.max_tokens=2048
        # which is too low for detailed tutoring responses
        if is_socratic_bots and max_tokens < SOCRATIC_MIN_MAX_TOKENS:
            max_tokens = SOCRATIC_MIN_MAX_TOKENS

        # Enforce minimum max_tokens when artifact-creating tools are loaded.
        # The artifact payload travels as the tool-call input, so a low cap
        # truncates the tool call mid-stream and the artifact is silently dropped.
        requested_slugs = message_data.get("dare_tool_slugs") or []
        if (
            any(slug in ARTIFACT_TOOL_SLUGS for slug in requested_slugs)
            and max_tokens < ARTIFACT_MIN_MAX_TOKENS
        ):
            max_tokens = ARTIFACT_MIN_MAX_TOKENS

        # Build generation config
        generation = GenerationConfig(
            temperature=message_data.get("temperature", 0.7),
            max_tokens=max_tokens,
            prompt_id=message_data.get("prompt_id"),
            web_search_enabled=message_data.get("web_search_enabled", False),
            web_fetch_enabled=message_data.get("web_fetch_enabled", False),
            image_generation_enabled=message_data.get(
                "image_generation_enabled", False
            ),
            image_generation_settings=message_data.get("image_generation_settings"),
            audio_transcription_enabled=message_data.get(
                "audio_transcription_enabled", False
            ),
            audio_transcription_settings=message_data.get(
                "audio_transcription_settings"
            ),
            structured_spec=message_data.get("structured_spec"),
            artifacts_enabled=message_data.get("artifacts_enabled", False),
        )

        # Build media config
        media = MediaConfig(
            images=message_data.get("images", []),
            media_ids=message_data.get("media_ids", []),
        )

        # Build Socratic config
        socratic = SocraticConfig(
            enabled=socratic_enabled,
            advanced_mode=bool(message_data.get("is_advanced")),
            bot_meta=bot_meta,
        )

        # Extract MCP server IDs from frontend payload
        mcp_server_ids = tuple(message_data.get("mcp_server_ids") or [])

        # Extract DARE tool slugs from frontend payload
        dare_tool_slugs = tuple(message_data.get("dare_tool_slugs") or [])

        # Build request
        request = LLMQueryRequest(
            message=message,
            conversation=conversation,
            user=user,
            llm=llm,
            context=context,
            generation=generation,
            media=media,
            socratic=socratic,
            message_obj=message_obj,
            workflow_run_step_obj=workflow_run_step_obj,
            mcp_server_ids=mcp_server_ids,
            dare_tool_slugs=dare_tool_slugs,
        )

        # Message-level settings from frontend always take precedence.
        # Keeping the call for code clarity and potential future use.
        if conversation:
            request = request.with_conversation_defaults(conversation)

        return request

    @staticmethod
    def from_workflow_data(
        message: str,
        user: Any,
        llm: Optional[Any] = None,
        file_ids: Optional[list] = None,
        embedding_ids: Optional[list] = None,
        tag_ids: Optional[list] = None,
        folder_ids: Optional[list] = None,
        prompt_id: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        max_context_snippets: int = 4,
        document_similarity_threshold: float = 0.5,
        workflow_run_step_obj: Optional[Any] = None,
        structured_spec: Optional[Dict[str, Any]] = None,
        web_search_enabled: bool = False,
        file_owner_id: Optional[int] = None,
    ) -> LLMQueryRequest:
        """Build LLMQueryRequest from workflow execution data.

        Args:
            message: User's message text
            user: User model instance
            llm: Optional LLM model instance
            file_ids: Full file content IDs
            embedding_ids: File IDs for semantic search
            tag_ids: Tag IDs to fetch files
            folder_ids: Folder IDs to fetch files
            prompt_id: Custom prompt template ID
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            max_context_snippets: Max snippets to retrieve
            document_similarity_threshold: Similarity threshold
            workflow_run_step_obj: WorkflowRunStep instance
            structured_spec: JSON schema for structured output
            web_search_enabled: Enable web search for this step
            file_owner_id: Original owner's user ID for cross-user embedding access

        Returns:
            Fully constructed LLMQueryRequest for workflow execution
        """
        context = ContextConfig(
            file_ids=file_ids or [],
            embedding_ids=embedding_ids or [],
            media_ids=[],  # Workflows don't use media files
            tag_ids=tag_ids or [],
            folder_ids=folder_ids or [],
            max_context_snippets=max_context_snippets,
            document_similarity_threshold=document_similarity_threshold,
            history_limit=0,  # Workflows don't use conversation history
            file_owner_id=file_owner_id,
        )

        generation = GenerationConfig(
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_id=prompt_id,
            structured_spec=structured_spec,
            web_search_enabled=web_search_enabled,
        )

        return LLMQueryRequest(
            message=message,
            conversation=None,  # Workflows don't have conversations
            user=user,
            llm=llm,
            context=context,
            generation=generation,
            workflow_run_step_obj=workflow_run_step_obj,
        )
