"""Context configuration DTO for LLM requests."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class ContextConfig:
    """Configuration for document context and retrieval.

    Controls how documents, embeddings, and conversation history are used
    to provide context to the LLM.

    Attributes:
        file_ids: Full file content IDs (read entire file)
        embedding_ids: File IDs for semantic search (RAG)
        media_ids: Media file IDs (images, videos, audio)
        tag_ids: Tag IDs to fetch associated files
        folder_ids: Folder IDs to fetch contained files
        referenced_conversation_ids: Previous conversation IDs for context
        referenced_conversation_history_limit: Max messages to include per referenced conversation
        referenced_summary_ids: Conversation summary IDs to include as context
        max_context_snippets: Maximum number of retrieved document snippets
        document_similarity_threshold: Minimum similarity score for retrieval
        history_limit: Number of conversation messages to include
    """
    file_ids: List[str] = field(default_factory=list)
    embedding_ids: List[str] = field(default_factory=list)
    file_owner_id: Optional[int] = None  # Bot creator's ID for shared access
    media_ids: List[str] = field(default_factory=list)
    tag_ids: List[str] = field(default_factory=list)
    folder_ids: List[str] = field(default_factory=list)
    referenced_conversation_ids: List[str] = field(default_factory=list)
    referenced_conversation_history_limit: int = 10
    referenced_summary_ids: List[int] = field(default_factory=list)
    max_context_snippets: int = 4
    document_similarity_threshold: float = 0.5
    history_limit: int = 20

    def has_any_context(self) -> bool:
        """Check if any context source is configured."""
        return bool(
            self.file_ids
            or self.embedding_ids
            or self.tag_ids
            or self.folder_ids
            or self.referenced_conversation_ids
            or self.referenced_summary_ids
        )
