import logging
from typing import Any, List, Dict
import io
import PyPDF2

from core.helpers.openai import OpenAIWrapper
from core.helpers.pinecone import PineconeClient
from files.models import File


logger = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(self):
        self.openai_client = OpenAIWrapper()
        self.pinecone_client = PineconeClient()

    def create_file_embeddings(self, file: File) -> bool:
        """
        Process a single file:
        1. Generate embeddings for the file content
        2. Store embeddings in Pinecone with proper metadata
        """
        try:
            content = self._read_file_content(file)
            chunks = self._chunk_text(content, chunk_size=1000)
            vectors = []
            for i, chunk in enumerate(chunks):
                embedding = self.openai_client.create_embeddings(chunk)

                metadata = {
                    'file_id': str(file.id),
                    'user_id': str(file.user.id),
                    'file_name': file.name or file.file.name,
                    'file_type': file.file_type,
                    'text': chunk,
                    'chunk_index': i
                }

                vector_id = f"file_{file.id}_chunk_{i}"
                vectors.append((vector_id, embedding, metadata))

            result = self.pinecone_client.upsert_vectors(
                vectors=vectors,
                namespace=f"user_{file.user.id}"
            )

            return True

        except Exception as e:
            raise Exception(str(e))

    def create_user_files_embeddings(self, user_id: int) -> bool:
        """Process all files belonging to a specific user"""
        try:
            files = File.objects.filter(user_id=user_id, is_deleted=False, is_active=True)
            if not files:
                return True

            for file in files:
                self.create_file_embeddings(file)

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

    async def search_similar_documents(self, query_text: str, file_ids: List[int], user_id: int, top_k: int = 5) -> str:
        """
        Search for similar document content using embeddings within specified files.
        """
        try:
            query_embedding = self.openai_client.create_embeddings(query_text)

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

            context_parts = []
            for match in results:
                metadata = match.get("metadata", {})
                text = metadata.get("text", "")
                file_name = metadata.get("file_name", "Unknown file")

                if text:
                    context_parts.append(f"From {file_name}:\n{text}")
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