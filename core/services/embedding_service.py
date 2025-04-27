from typing import Dict, List, Tuple
from core.config.vector_db import create_vector_id

class EmbeddingService:
    """Service for creating and managing embeddings."""

    def __init__(self, embedding_client):
        """
        Initialize with an embedding client that provides create_embeddings
        and create_batch_embeddings methods.
        """
        self.embedding_client = embedding_client

    def create_embeddings_with_metadata(
        self,
        chunks: List[str],
        file_id: int,
        user_id: int,
        file_name: str,
        file_type: str
    ) -> List[Tuple[str, List[float], Dict]]:
        """Create embeddings for text chunks with metadata."""
        embeddings = self.embedding_client.create_batch_embeddings(chunks)

        vectors: List[Tuple[str, List[float], Dict]] = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            vector_id = create_vector_id(file_id, i)
            metadata = {
                'file_id': str(file_id),
                'user_id': str(user_id),
                'file_name': file_name,
                'file_type': file_type,
                'text': chunk,
                'chunk_index': i
            }
            vectors.append((vector_id, embedding, metadata))

        return vectors