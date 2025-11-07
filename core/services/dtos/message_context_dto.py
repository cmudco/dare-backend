"""Message context DTO for Socratic message building."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MessageBuildContext:
    """Context data for building Socratic/Advanced messages.

    This DTO encapsulates all the parameters needed for the internal
    _build_socratic_messages and _build_advanced_messages methods.

    Attributes:
        message: User's input message
        conversation: Conversation model instance
        user_id: User ID for file access and vector service
        embedding_ids: File IDs for semantic search
        max_context_snippets: Maximum number of snippets to retrieve
        document_similarity_threshold: Similarity threshold for retrieval
        history_limit: Number of conversation messages to include
        message_obj: Optional Message model instance for tracking
        workflow_run_step_obj: Optional WorkflowRunStep for execution tracking
        subject: Subject from bot metadata
        topic: Topic from bot metadata
        title: Title from bot metadata (for advanced mode)
        learning_goals: Learning goals from bot metadata
        chat_prompt: Chat prompt from bot metadata
    """
    message: str
    conversation: Any  # Conversation model
    user_id: int
    embedding_ids: list
    max_context_snippets: int
    document_similarity_threshold: float
    history_limit: int
    subject: str
    topic: str
    learning_goals: str
    chat_prompt: str
    message_obj: Optional[Any] = None  # Message model
    workflow_run_step_obj: Optional[Any] = None  # WorkflowRunStep model
    title: str = ""  # Used in advanced mode

    @classmethod
    def from_request(cls, request: 'LLMQueryRequest') -> 'MessageBuildContext':
        """Create MessageBuildContext from LLMQueryRequest.

        Args:
            request: LLMQueryRequest containing all query parameters

        Returns:
            MessageBuildContext with extracted socratic data
        """
        return cls(
            message=request.message,
            conversation=request.conversation,
            user_id=request.user.id if request.user else None,
            embedding_ids=request.context.embedding_ids,
            max_context_snippets=request.context.max_context_snippets,
            document_similarity_threshold=request.context.document_similarity_threshold,
            history_limit=request.context.history_limit,
            message_obj=request.message_obj,
            workflow_run_step_obj=request.workflow_run_step_obj,
            subject=request.socratic.get_subject(),
            topic=request.socratic.get_topic(),
            title=request.socratic.get_title(),
            learning_goals=request.socratic.get_learning_goals(),
            chat_prompt=request.socratic.get_chat_prompt(),
        )
