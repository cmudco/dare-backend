import logging
from typing import List, Tuple
import io
import PyPDF2

from core.helpers.openai import OpenAIWrapper
from core.helpers.pinecone import PineconeClient
from files.models import File
from conversations.models import Snippet
from channels.db import database_sync_to_async  # Add this import

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000
BATCH_SIZE = 100

class DocumentProcessor:
    def __init__(self):
        self.openai_client = OpenAIWrapper()
        self.pinecone_client = PineconeClient()

    def create_file_embeddings(self, file: File) -> int:
        """
        Process a single file:
        1. Generate embeddings for all chunks in a single OpenAI request
        2. Split and store embeddings in Pinecone with proper metadata
        """
        try:
            content = self._read_file_content(file)
            chunks = self._chunk_text(content, chunk_size=CHUNK_SIZE)
            embeddings = self.openai_client.create_batch_embeddings(chunks)

            if len(chunks) != len(embeddings):
                logger.warning(f"Mismatch: {len(chunks)} chunks, {len(embeddings)} embeddings for file {file.id}")

            vectors: List[Tuple[str, list, dict]] = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                vector_id = f"file_{file.id}_chunk_{i}"
                metadata = {
                    'file_id': str(file.id),
                    'user_id': str(file.user.id),
                    'file_name': file.name or file.file.name,
                    'file_type': file.file_type,
                    'text': chunk,
                    'chunk_index': i
                }
                vectors.append((vector_id, embedding, metadata))

            for i in range(0, len(vectors), BATCH_SIZE):
                batch = vectors[i:i + BATCH_SIZE]
                self.pinecone_client.upsert_vectors(
                    vectors=batch,
                    namespace=f"user_{file.user.id}"
                )

            return len(vectors)

        except ValueError as ve:
            logger.exception(f"Invalid data processing file {file.id}")
            raise Exception(f"Invalid data: {str(ve)}")
        except Exception as e:
            logger.exception(f"Error processing file {file.id}")
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
                    logger.error(f"Failed to process file {file.id}: {str(e)}")
                    continue

            return True

        except Exception as e:
            logger.exception(f"Error processing user files: {str(e)}")
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

    def _read_file_content(self, file: File) -> str:
        """Read and extract content from various file types"""
        try:
            file_name = file.file.name.lower()

            if file_name.endswith('.pdf'):
                with file.file.open('rb') as f:
                    pdf_reader = PyPDF2.PdfReader(io.BytesIO(f.read()))
                    text_content = []
                    for page in pdf_reader.pages:
                        text_content.append(page.extract_text())
                    return ' '.join(text_content)

            elif file_name.endswith(('.txt', '.md', '.json')):
                with file.file.open('r') as f:
                    return f.read()

            else:
                return f"File: {file.name or file.file.name}"

        except Exception as e:
            raise Exception(f"Error reading file content: {str(e)}")

    async def _save_snippets(self, snippets_to_save, message_obj):
        """
        Save retrieved snippets to the database.
        """
        try:
            for snippet_data in snippets_to_save:
                file = await database_sync_to_async(File.active_objects.get)(id=snippet_data["file_id"])
                await database_sync_to_async(Snippet.active_objects.create)(
                    message=message_obj,
                    file=file,
                    text=snippet_data["text"],
                    similarity_score=snippet_data["similarity_score"],
                    chunk_index=snippet_data["chunk_index"]
                )
        except Exception as e:
            logger.exception(f"Error saving snippets for message {message_obj.id}: {str(e)}")

    async def search_similar_documents(
        self,
        query_text: str,
        file_ids: List[int],
        user_id: int,
        top_k: int = 10,
        similarity_threshold: float = 0.5,
        message_obj=None
    ) -> str:
        """
        Search for similar documents in Pinecone based on the query text.
        Simplified to a single query with a similarity threshold.
        Logs retrieved snippets and stores them in the Snippet model.
        """
        try:
            query_embedding = self.openai_client.create_embeddings(query_text)
            context_parts = []
            snippets_to_save = []

            if not file_ids:
                logger.info("No file IDs provided for vector search.")
                return ""

            filter_query = {
                "user_id": str(user_id),
                "file_id": {"$in": [str(file_id) for file_id in file_ids]}
            }

            results = self.pinecone_client.query_vectors(
                vector=query_embedding,
                top_k=top_k,
                namespace=f"user_{user_id}",
                filter=filter_query
            )

            for match in results:
                print(match, similarity_threshold)
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

        except Exception as e:
            logger.exception(f"Error retrieving document context: {str(e)}")
            return ""

    def delete_file_vectors(self, file_id: int, user_id: int) -> bool:
        """Delete all vectors related to a specific file"""
        try:
            filter_query = {"file_id": str(file_id)}

            results = self.pinecone_client.query_vectors(
                vector=[0] * 3072,
                filter=filter_query,
                top_k=1000,
                namespace=f"user_{user_id}"
            )

            if results:
                vector_ids = [match['id'] for match in results]

                self.pinecone_client.delete_vectors(
                    ids=vector_ids,
                    namespace=f"user_{user_id}"
                )

            return True

        except Exception as e:
            raise Exception(f"Error deleting file vectors: {str(e)}")