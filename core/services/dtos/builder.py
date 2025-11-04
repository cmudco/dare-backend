"""Builder pattern for constructing LLMQueryRequest from dictionaries."""

from typing import Dict, Any, Optional

from users.constants import AuthSourceChoice
from .request_dto import LLMQueryRequest
from .context_dto import ContextConfig
from .generation_dto import GenerationConfig
from .media_dto import MediaConfig
from .socratic_dto import SocraticConfig


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
        # Build context config
        context = ContextConfig(
            file_ids=message_data.get("file_ids", []),
            embedding_ids=message_data.get("embedding_ids", []),
            tag_ids=message_data.get("tag_ids", []),
            folder_ids=message_data.get("folder_ids", []),
            referenced_conversation_ids=message_data.get("referenced_conversation_ids", []),
            max_context_snippets=message_data.get("max_context_snippets", 4),
            document_similarity_threshold=message_data.get("document_similarity_threshold", 0.5),
            history_limit=message_data.get("history_limit", 20),
        )

        # Build generation config
        generation = GenerationConfig(
            temperature=message_data.get("temperature", 0.7),
            max_tokens=message_data.get("max_tokens", 8000),
            prompt_id=message_data.get("prompt_id"),
            web_search_enabled=message_data.get("web_search_enabled", False),
            image_generation_enabled=message_data.get("image_generation_enabled", False),
            image_generation_settings=message_data.get("image_generation_settings"),
            structured_spec=message_data.get("structured_spec"),
        )

        # Build media config
        media = MediaConfig(
            images=message_data.get("images", []),
            media_ids=message_data.get("media_ids", []),
        )

        # Build Socratic config
        is_socratic_bots = platform == AuthSourceChoice.SOCRATIC_BOTS if platform else False
        socratic = SocraticConfig(
            enabled=is_socratic_bots and not message_data.get("prompt_id"),
            advanced_mode=bool(message_data.get("is_advanced")),
            bot_meta=message_data.get("bot_meta", {}),
        )

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

        Returns:
            Fully constructed LLMQueryRequest for workflow execution
        """
        context = ContextConfig(
            file_ids=file_ids or [],
            embedding_ids=embedding_ids or [],
            tag_ids=tag_ids or [],
            folder_ids=folder_ids or [],
            max_context_snippets=max_context_snippets,
            document_similarity_threshold=document_similarity_threshold,
            history_limit=0,  # Workflows don't use conversation history
        )

        generation = GenerationConfig(
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_id=prompt_id,
            structured_spec=structured_spec,
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
