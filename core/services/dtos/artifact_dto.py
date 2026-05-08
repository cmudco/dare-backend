"""Artifact context DTO for artifact generation/modification.

Contains context configuration for passing RAG, files, system prompts,
and parent content to artifact workflows.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

from core.services.dtos.context_dto import ContextConfig


@dataclass(frozen=True)
class ArtifactContext:
    """Context for artifact generation/modification.

    This DTO packages all external context that should be available
    during artifact creation or modification.

    Attributes:
        context_config: RAG/files configuration (embeddings, files, etc.)
        system_prompt: Custom system prompt to append to artifact prompts
        full_parent_content: Complete parent artifact content for modifications
        media_ids: Media file IDs (images, etc.)
    """

    context_config: Optional[ContextConfig] = None
    system_prompt: Optional[str] = None
    full_parent_content: Optional[str] = None
    media_ids: List[str] = field(default_factory=list)

    def has_rag_context(self) -> bool:
        """Check if RAG context is configured."""
        return self.context_config is not None and self.context_config.has_any_context()

    def has_system_prompt(self) -> bool:
        """Check if a custom system prompt is provided."""
        return bool(self.system_prompt)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for state serialization."""
        result = {
            "system_prompt": self.system_prompt,
            "full_parent_content": self.full_parent_content,
            "media_ids": self.media_ids,
        }
        if self.context_config:
            result["context_config"] = asdict(self.context_config)
        else:
            result["context_config"] = None
        return result

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["ArtifactContext"]:
        """Create from dictionary (for state deserialization)."""
        if not data:
            return None

        context_config = None
        if data.get("context_config"):
            context_config = ContextConfig(**data["context_config"])

        return cls(
            context_config=context_config,
            system_prompt=data.get("system_prompt"),
            full_parent_content=data.get("full_parent_content"),
            media_ids=data.get("media_ids", []),
        )


def build_artifact_context(
    file_ids: Optional[List[str]] = None,
    embedding_ids: Optional[List[str]] = None,
    tag_ids: Optional[List[str]] = None,
    folder_ids: Optional[List[str]] = None,
    media_ids: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
    full_parent_content: Optional[str] = None,
    max_context_snippets: int = 4,
    document_similarity_threshold: float = 0.5,
) -> ArtifactContext:
    """Factory function to build ArtifactContext from individual parameters.

    This is a convenience function to avoid constructing ContextConfig separately.

    Args:
        file_ids: Full file content IDs
        embedding_ids: File IDs for semantic search (RAG)
        tag_ids: Tag IDs to fetch associated files
        folder_ids: Folder IDs to fetch contained files
        media_ids: Media file IDs (images, etc.)
        system_prompt: Custom system prompt to append
        full_parent_content: Parent artifact content (for modifications)
        max_context_snippets: Max RAG snippets
        document_similarity_threshold: Min similarity for retrieval

    Returns:
        Configured ArtifactContext
    """
    context_config = None
    if any([file_ids, embedding_ids, tag_ids, folder_ids]):
        context_config = ContextConfig(
            file_ids=file_ids or [],
            embedding_ids=embedding_ids or [],
            tag_ids=tag_ids or [],
            folder_ids=folder_ids or [],
            max_context_snippets=max_context_snippets,
            document_similarity_threshold=document_similarity_threshold,
        )

    return ArtifactContext(
        context_config=context_config,
        system_prompt=system_prompt,
        full_parent_content=full_parent_content,
        media_ids=media_ids or [],
    )
