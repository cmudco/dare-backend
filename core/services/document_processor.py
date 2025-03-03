from typing import Any, List, Dict
import io
import PyPDF2

from core.helpers.openai import OpenAIWrapper
from core.helpers.pinecone import PineconeClient
from files.models import File


class DocumentProcessor:
    def __init__(self):
        self.openai_client = OpenAIWrapper()
        self.pinecone_client = PineconeClient()

    def process_file(self, file: File) -> bool:
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
                embedding = self.openai_client.generate_embedding(chunk)

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
            print(f"Error in process_file: {str(e)}")
            raise

    def process_user_files(self, user_id: int) -> bool:
        """Process all files belonging to a specific user"""
        try:
            files = File.objects.filter(user_id=user_id, is_deleted=False, is_active=True)
            if not files:
                return True

            for file in files:
                self.process_file(file)

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

    def search_similar_content(self, query_text: str, user_id: int, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar content using embeddings within a user's namespace
        """
        try:
            query_embedding = self.openai_client.generate_embedding(query_text)

            results = self.pinecone_client.query_vectors(
                vector=query_embedding,
                top_k=top_k,
                namespace=f"user_{user_id}"
            )

            return results

        except Exception as e:
            raise Exception(f"Error searching content: {str(e)}")

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