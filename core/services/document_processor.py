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
        1. Generate embeddings for all chunks in a single OpenAI request
        2. Split and store embeddings in Pinecone with proper metadata
        """
        try:
            content = self._read_file_content(file)
            chunks = self._chunk_text(content, chunk_size=1000)
            embeddings_response = self.openai_client.create_batch_embeddings(chunks)
            
            vectors = []
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings_response)):
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

            # Process vectors in batches to avoid Pinecone size limits
            batch_size = 50
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                try:
                    self.pinecone_client.upsert_vectors(
                        vectors=batch,
                        namespace=f"user_{file.user.id}"
                    )
                except Exception as e:
                    logger.error(f"Error upserting batch {i//batch_size + 1}: {str(e)}")
                    raise

            logger.info(f"Completed processing file: {file.id}")
            return True

        except Exception as e:
            logger.exception(f"Error processing file {file.id}")
            raise Exception(f"Error processing file: {str(e)}")

    def create_user_files_embeddings(self, user_id: int) -> bool:
        """Process all files belonging to a specific user"""
        try:
            files = File.objects.filter(user_id=user_id, is_deleted=False, is_active=True)
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

    async def search_similar_documents(self, query_text: str, file_ids: List[int], user_id: int, top_k: int = 10) -> str:
        try:
            query_embedding = self.openai_client.create_embeddings(query_text)
            context_parts = []

            for file_id in file_ids:
                filter_query = {
                    "user_id": str(user_id),
                    "file_id": str(file_id)
                }
                results = self.pinecone_client.query_vectors(
                    vector=query_embedding,
                    top_k=max(1, top_k // len(file_ids)),
                    namespace=f"user_{user_id}",
                    filter=filter_query
                )
                for match in results:
                    metadata = match.get("metadata", {})
                    text = metadata.get("text", "")
                    file_name = metadata.get("file_name", "Unknown file")
                    if text:
                        context_parts.append(f"From {file_name}:\n{text}")

            if len(context_parts) < top_k and len(file_ids) > 1:
                filter_query = {
                    "user_id": str(user_id),
                    "file_id": {"$in": [str(file_id) for file_id in file_ids]}
                }
                extra_results = self.pinecone_client.query_vectors(
                    vector=query_embedding,
                    top_k=top_k - len(context_parts),
                    namespace=f"user_{user_id}",
                    filter=filter_query
                )
                for match in extra_results:
                    if match["id"] not in [part.split("\n")[1] for part in context_parts]:
                        metadata = match.get("metadata", {})
                        text = metadata.get("text", "")
                        file_name = metadata.get("file_name", "Unknown file")
                        if text:
                            context_parts.append(f"From {file_name}:\n{text}")

            return "\n\n".join(context_parts[:top_k])

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