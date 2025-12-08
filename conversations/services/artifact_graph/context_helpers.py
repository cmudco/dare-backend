"""
Context Helpers for Artifact Generation

Provides helper functions to retrieve RAG context (embeddings, files)
for use in artifact nodes.
"""

import logging
from typing import Optional, List, Dict, Any

from channels.db import database_sync_to_async

from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from core.services.vector_service import get_vector_service_async
from core.services.dtos.artifact_dto import ArtifactContext
from files.models import File


logger = logging.getLogger(__name__)


class ArtifactContextRetriever:
    """Retrieves RAG context for artifact generation.
    
    Uses the same pattern as LLMService but simplified for artifact workflows.
    """
    
    def __init__(self):
        self.document_processor = DocumentProcessor(vector_service=None)
        self.file_processor = FileProcessor()
        
    async def get_context_for_artifact(
        self,
        artifact_context: Optional[Dict[str, Any]],
        query_text: str,
        user_id: Optional[int] = None,
    ) -> Optional[str]:
        """Retrieve RAG context snippets for artifact generation.
        
        Args:
            artifact_context: Serialized ArtifactContext dict from state
            query_text: Query text for semantic search (usually user message)
            user_id: Optional user ID for filtering files
            
        Returns:
            Formatted context string, or None if no context available
        """
        if not artifact_context:
            return None
            
        # Deserialize artifact context
        ctx = ArtifactContext.from_dict(artifact_context)
        if not ctx or not ctx.has_rag_context():
            return None
            
        context_config = ctx.context_config
        context_parts = []
        
        # 1. Get full file contents if file_ids specified
        if context_config.file_ids:
            file_contents = await self._get_full_file_contents(context_config.file_ids)
            context_parts.extend(file_contents)
            
        # 2. Get semantic search snippets from embeddings
        all_embedding_file_ids = set(context_config.embedding_ids or [])
        
        # Expand tag_ids to file IDs
        if context_config.tag_ids and user_id:
            tagged_file_ids = await self._get_files_from_tags(
                context_config.tag_ids, user_id
            )
            all_embedding_file_ids.update(tagged_file_ids)
            
        # Expand folder_ids to file IDs
        if context_config.folder_ids and user_id:
            folder_file_ids = await self._get_files_from_folders(
                context_config.folder_ids, user_id
            )
            all_embedding_file_ids.update(folder_file_ids)
            
        # Run semantic search if we have embedding file IDs
        if all_embedding_file_ids:
            if user_id and user_id != self.document_processor.user_id:
                self.document_processor.user_id = user_id
                self.document_processor.vector_service = await get_vector_service_async(user_id)
                
            snippets = await self.document_processor.search_similar_documents(
                query_text=query_text,
                file_ids=list(all_embedding_file_ids),
                user_id=user_id,
                top_k=context_config.max_context_snippets,
                similarity_threshold=context_config.document_similarity_threshold,
            )
            
            if snippets:
                context_parts.append(snippets)
                
        if not context_parts:
            return None
            
        # Format as a context block
        formatted_context = "\n\n".join(context_parts)
        return f"=== Relevant Context ===\n{formatted_context}\n=== End Context ===\n"
        
    @database_sync_to_async
    def _get_full_file_contents(self, file_ids: List[str]) -> List[str]:
        """Read full content from files."""
        if not file_ids:
            return []
            
        file_contents = []
        files = File.active_objects.filter(id__in=file_ids)
        
        for file in files:
            try:
                content = self.file_processor.read_file_content(file)
                file_name = file.name or file.file.name
                formatted = f"File: {file_name}\n{content}"
                file_contents.append(formatted)
            except Exception as e:
                logger.warning(f"Error reading file {file.id}: {e}")
                continue
                
        return file_contents
        
    @database_sync_to_async
    def _get_files_from_tags(self, tag_ids: List[str], user_id: int) -> List[str]:
        """Get file IDs from tags."""
        if not tag_ids:
            return []
        return list(
            File.active_objects.filter(tags__id__in=tag_ids, user_id=user_id)
            .distinct()
            .values_list('id', flat=True)
        )
        
    @database_sync_to_async
    def _get_files_from_folders(self, folder_ids: List[str], user_id: int) -> List[str]:
        """Get file IDs from folders."""
        if not folder_ids:
            return []
        return list(
            File.active_objects.filter(folders__id__in=folder_ids, user_id=user_id)
            .distinct()
            .values_list('id', flat=True)
        )


# Singleton instance for reuse
_context_retriever = None


async def get_artifact_context_retriever() -> ArtifactContextRetriever:
    """Get singleton instance of ArtifactContextRetriever."""
    global _context_retriever
    if _context_retriever is None:
        _context_retriever = ArtifactContextRetriever()
    return _context_retriever


async def retrieve_rag_context_for_artifact(
    artifact_context: Optional[Dict[str, Any]],
    query_text: str,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """Convenience function to retrieve RAG context.
    
    This is the main function artifact nodes should call.
    
    Args:
        artifact_context: Serialized ArtifactContext dict from state
        query_text: Query text for semantic search
        user_id: Optional user ID
        
    Returns:
        Formatted context string or None
    """
    retriever = await get_artifact_context_retriever()
    return await retriever.get_context_for_artifact(
        artifact_context=artifact_context,
        query_text=query_text,
        user_id=user_id,
    )
