from typing import Dict, List, Tuple
import io
import PyPDF2
from core.helpers.openai import OpenAIWrapper
from core.services.vector_service import get_vector_service
from core.services.embedding_service import EmbeddingService
from core.services.file_processor import FileProcessor
from files.models import File
from conversations.models import Snippet
from channels.db import database_sync_to_async
from core.config.vector_db import get_user_namespace
from core.config.processing import CHUNK_SIZE, BATCH_SIZE, DEFAULT_SIMILARITY_THRESHOLD, DEFAULT_TOP_K

class DocumentProcessor:
    def __init__(self, openai_client=None, vector_service=None, embedding_service=None, file_processor=None):
        self.openai_client = openai_client or OpenAIWrapper()
        self.vector_service = vector_service or get_vector_service()
        self.embedding_service = embedding_service or EmbeddingService(self.openai_client)
        self.file_processor = file_processor or FileProcessor()

    def create_file_embeddings(self, file: File) -> int:
        """
        Process a single file:
        1. Generate embeddings for all chunks in a single OpenAI request
        2. Split and store embeddings in the vector database with proper metadata
        """
        try:
            content = self.file_processor.read_file_content(file)
            vectors = self._process_chunks(content, file)
            self._store_vectors(vectors, file.user.id)
            return len(vectors)

        except Exception as e:
            raise Exception(f"Error processing file: {str(e)}")

    def create_user_files_embeddings(self, user_id: int) -> bool:
        """Process all files belonging to a specific user"""
        try:
            files = File.active_objects.filter(user_id=user_id, is_deleted=False, is_active=True)
            if not files:
                return True

            for file in files:
                try:
                    self.create_file_embeddings(file)
                except Exception as e:
                    continue

            return True

        except Exception as e:
            raise Exception(f"Error processing user files: {str(e)}")

    def _chunk_text(self, text: str, chunk_size: int = 1000) -> List[str]:
        """Split text into smaller chunks."""
        words = text.split()
        chunks = []
        current_chunk = []
        current_size = 0

        for word in words:
            current_size += len(word) + 1
            if current_size > chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = [word]
                current_size = len(word)
            else:
                current_chunk.append(word)

        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _process_chunks(self, content: str, file: File) -> List[Tuple[str, List[float], Dict]]:
        """Process file content into chunks and generate vectors."""
        chunks = self._chunk_text(content, chunk_size=CHUNK_SIZE)
        return self.embedding_service.create_embeddings_with_metadata(
            chunks,
            file.id,
            file.user.id,
            file.name or file.file.name,
            file.file_type
        )

    def _store_vectors(self, vectors: List[Tuple[str, List[float], Dict]], user_id: int) -> bool:
        """Store vectors in batches."""
        for i in range(0, len(vectors), BATCH_SIZE):
            batch = vectors[i:i + BATCH_SIZE]
            self.vector_service.upsert_vectors(
                vectors=batch,
                namespace=get_user_namespace(user_id)
            )
        return True

    async def _save_snippets(self, snippets_to_save, message_obj):
        """Save retrieved snippets to the database."""
        try:
            successful_saves = 0
            for i, snippet_data in enumerate(snippets_to_save):
                try:
                    file_id = snippet_data["file_id"]
                    file = await database_sync_to_async(File.active_objects.get)(id=file_id)
                    snippet = await database_sync_to_async(Snippet.active_objects.create)(
                        message=message_obj,
                        file=file,
                        text=snippet_data["text"],
                        similarity_score=snippet_data["similarity_score"],
                        chunk_index=snippet_data["chunk_index"]
                    )
                    successful_saves += 1
                except File.DoesNotExist:
                    pass
                except Exception as e:
                    pass
        except Exception as e:
            pass

    async def search_similar_documents(
        self,
        query_text: str,
        file_ids: List[int],
        user_id: int,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        message_obj=None
    ) -> str:
        """Search for similar documents based on the query text."""
        if not file_ids:
            return ""

        try:
            query_embedding = self.openai_client.create_embeddings(query_text)

            results = self.vector_service.search_documents(
                vector=query_embedding,
                user_id=user_id,
                file_ids=file_ids,
                top_k=top_k
            )

            return await self._process_search_results(
                results,
                similarity_threshold,
                message_obj
            )
        except Exception as e:
            return ""

    async def _process_search_results(
        self,
        results: List[Dict],
        similarity_threshold: float,
        message_obj=None
    ) -> str:
        """Process search results and collect context."""
        context_parts = []
        snippets_to_save = []

        for match in results:
            score = match.get("score", 0.0)
            if score < similarity_threshold:
                continue

            metadata = match.get("metadata", {})
            text = metadata.get("text", "")
            file_id = metadata.get("file_id", "")
            file_name = metadata.get("file_name", "Unknown file")
            chunk_index = metadata.get("chunk_index", 0)

            if text:
                context_parts.append(f"From {file_name}:\n{text}")

                if message_obj:
                    snippets_to_save.append({
                        "message": message_obj,
                        "file_id": file_id,
                        "text": text,
                        "similarity_score": score,
                        "chunk_index": chunk_index
                    })

        if snippets_to_save and message_obj:
            await self._save_snippets(snippets_to_save, message_obj)

        return "\n\n".join(context_parts)

    def delete_file_vectors(self, file_id: int, user_id: int) -> bool:
        """Delete all vectors related to a specific file"""
        try:
            return self.vector_service.delete_file_vectors(file_id, user_id)
        except Exception as e:
            raise Exception(f"Error deleting file vectors: {str(e)}")