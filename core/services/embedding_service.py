from typing import Dict, List, Tuple
import tiktoken
from core.config.vector_db import create_vector_id

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
        """Count tokens in a text string using tiktoken."""
        return len(self.tokenizer.encode(text))

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

            if current_batch and (current_batch_tokens + chunk_tokens) > self.max_tokens_per_request:
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
        file_type: str
    ) -> List[Tuple[str, List[float], Dict]]:
        """Create embeddings for text chunks with metadata, processing in token-safe batches."""
        if not chunks:
            return []

        total_tokens = sum(self._count_tokens(chunk) for chunk in chunks)

        chunk_batches = self._batch_chunks_by_tokens(chunks)

        vectors: List[Tuple[str, List[float], Dict]] = []
        chunk_index = 0

        for batch_num, batch_chunks in enumerate(chunk_batches):
            batch_tokens = sum(self._count_tokens(chunk) for chunk in batch_chunks)

            if batch_tokens > self.max_tokens_per_request:
                for chunk_idx, chunk in enumerate(batch_chunks):
                    chunk_tokens = self._count_tokens(chunk)
                    if chunk_tokens <= self.max_tokens_per_request:
                        try:
                            single_embedding = self.embedding_client.create_batch_embeddings([chunk])
                            vector_id = create_vector_id(file_id, chunk_index)
                            metadata = {
                                'file_id': str(file_id),
                                'user_id': str(user_id),
                                'file_name': file_name,
                                'file_type': file_type,
                                'text': chunk,
                                'chunk_index': chunk_index
                            }
                            vectors.append((vector_id, single_embedding[0], metadata))
                            chunk_index += 1
                        except Exception as e:
                            pass
                    else:
                        chunk_index += 1
                continue

            try:
                batch_embeddings = self.embedding_client.create_batch_embeddings(batch_chunks)

                for chunk, embedding in zip(batch_chunks, batch_embeddings):
                    vector_id = create_vector_id(file_id, chunk_index)
                    metadata = {
                        'file_id': str(file_id),
                        'user_id': str(user_id),
                        'file_name': file_name,
                        'file_type': file_type,
                        'text': chunk,
                        'chunk_index': chunk_index
                    }
                    vectors.append((vector_id, embedding, metadata))
                    chunk_index += 1

            except Exception as e:
                raise Exception(f"Error processing batch {batch_num + 1}/{len(chunk_batches)}: {str(e)}")

        return vectors