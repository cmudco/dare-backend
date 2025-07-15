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
        # OpenAI's token limit for embeddings API with larger safety margin
        self.max_tokens_per_request = 250000  # Using 250k for larger safety buffer
        # Initialize tokenizer for accurate token counting
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

            # Check if a single chunk exceeds the limit
            if chunk_tokens > self.max_tokens_per_request:
                print(f"WARNING: Single chunk has {chunk_tokens:,} tokens, exceeding limit of {self.max_tokens_per_request:,}")
                # If we have a current batch, save it first
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_batch_tokens = 0
                # Put the oversized chunk in its own batch (will likely fail, but we'll handle it)
                batches.append([chunk])
                continue

            # If adding this chunk would exceed the limit, start a new batch
            if current_batch and (current_batch_tokens + chunk_tokens) > self.max_tokens_per_request:
                batches.append(current_batch)
                current_batch = [chunk]
                current_batch_tokens = chunk_tokens
            else:
                current_batch.append(chunk)
                current_batch_tokens += chunk_tokens

        # Add the last batch if it has chunks
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

        # Calculate total tokens for logging
        total_tokens = sum(self._count_tokens(chunk) for chunk in chunks)
        print(f"Processing {len(chunks)} chunks with {total_tokens:,} total tokens for file: {file_name}")

        # Split chunks into token-safe batches
        chunk_batches = self._batch_chunks_by_tokens(chunks)
        print(f"Split into {len(chunk_batches)} batches to stay within token limits")

        vectors: List[Tuple[str, List[float], Dict]] = []
        chunk_index = 0

        for batch_num, batch_chunks in enumerate(chunk_batches):
            batch_tokens = sum(self._count_tokens(chunk) for chunk in batch_chunks)
            print(f"Processing batch {batch_num + 1}/{len(chunk_batches)} with {len(batch_chunks)} chunks ({batch_tokens:,} tokens)")

            # Double-check batch size before sending to API
            if batch_tokens > self.max_tokens_per_request:
                print(f"ERROR: Batch {batch_num + 1} has {batch_tokens:,} tokens, exceeding limit of {self.max_tokens_per_request:,}")

                # Try to process chunks individually if batch is too large
                print(f"Attempting to process {len(batch_chunks)} chunks individually...")
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
                            print(f"  ✓ Processed individual chunk {chunk_idx + 1}/{len(batch_chunks)}")
                        except Exception as e:
                            print(f"  ✗ Failed to process individual chunk {chunk_idx + 1}: {str(e)}")
                            # Continue with other chunks
                    else:
                        print(f"  ⚠ Skipping chunk {chunk_idx + 1} - too large ({chunk_tokens:,} tokens)")
                        chunk_index += 1  # Still increment to maintain order
                continue

            try:
                # Process this batch normally
                batch_embeddings = self.embedding_client.create_batch_embeddings(batch_chunks)

                # Create vectors for this batch
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
                print(f"Error processing batch {batch_num + 1}: {str(e)}")
                raise Exception(f"Error processing batch {batch_num + 1}/{len(chunk_batches)}: {str(e)}")

        print(f"Successfully created {len(vectors)} embeddings for file: {file_name}")
        return vectors