from typing import Dict, List, Tuple

import tiktoken

from core.config.vector_db import create_vector_id
from core.services.vector_integrity import VectorIntegrityError


class EmbeddingService:
    """Service for creating and managing embeddings."""

    def __init__(self, embedding_client):
        """
        Initialize with an embedding client that provides create_embeddings
        and create_batch_embeddings methods.
        """
        self.embedding_client = embedding_client
        self.max_tokens_per_request = 250000
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        """Count document text without interpreting tokenizer sentinel strings."""
        return len(self.tokenizer.encode(text, disallowed_special=()))

    def _batch_chunks_by_tokens(self, chunks: List[str]) -> List[List[str]]:
        """
        Split chunks into batches that fit within the token limit.
        Returns a list of chunk batches.
        """
        batches = []
        current_batch = []
        current_batch_tokens = 0

        for chunk in chunks:
            chunk_tokens = self._count_tokens(chunk)

            if chunk_tokens > self.max_tokens_per_request:
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_batch_tokens = 0
                batches.append([chunk])
                continue

            if (
                current_batch
                and (current_batch_tokens + chunk_tokens) > self.max_tokens_per_request
            ):
                batches.append(current_batch)
                current_batch = [chunk]
                current_batch_tokens = chunk_tokens
            else:
                current_batch.append(chunk)
                current_batch_tokens += chunk_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def create_embeddings_with_metadata(
        self,
        chunks: List[str],
        file_id: int,
        user_id: int,
        file_name: str,
        file_type: str,
    ) -> List[Tuple[str, List[float], Dict]]:
        """Create embeddings for text chunks with metadata, processing in token-safe batches."""
        if not chunks:
            return []

        chunk_batches = self._batch_chunks_by_tokens(chunks)
        vectors = []
        for batch in chunk_batches:
            if any(
                self._count_tokens(chunk) > self.max_tokens_per_request
                for chunk in batch
            ):
                error = VectorIntegrityError(
                    "A chunk exceeds the embedding request token limit"
                )
                error.generated_count = len(vectors)
                raise error
            try:
                embeddings = self.embedding_client.create_batch_embeddings(batch)
            except Exception as error:
                error.generated_count = len(vectors)
                raise
            if len(embeddings) != len(batch):
                error = VectorIntegrityError(
                    "Embedding provider returned an incomplete or excessive batch"
                )
                error.generated_count = len(vectors) + len(embeddings)
                raise error
            for chunk, embedding in zip(batch, embeddings):
                index = len(vectors)
                metadata = {
                    "file_id": str(file_id),
                    "user_id": str(user_id),
                    "file_name": file_name,
                    "file_type": file_type,
                    "text": chunk,
                    "chunk_index": index,
                }
                vectors.append((create_vector_id(file_id, index), embedding, metadata))
        return vectors
