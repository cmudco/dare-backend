from typing import Dict, List, Tuple
from core.helpers.openai import OpenAIWrapper
from core.services.vector_service import get_vector_service
from core.services.embedding_service import EmbeddingService
from core.services.file_processor import FileProcessor
from files.models import File
from conversations.models import Snippet
from channels.db import database_sync_to_async
from core.config.vector_db import get_user_namespace
from core.config.processing import CHUNK_SIZE, BATCH_SIZE, DEFAULT_SIMILARITY_THRESHOLD, DEFAULT_TOP_K, OVERLAP_SIZE
from langchain_text_splitters import RecursiveCharacterTextSplitter
from workflows.models import WorkflowStepSnippet

class DocumentProcessor:
    def __init__(self, openai_client=None, vector_service=None, embedding_service=None, file_processor=None, user_id=None):
        self.openai_client = openai_client or OpenAIWrapper()
        self.user_id = user_id
        self.vector_service = vector_service
        self.embedding_service = embedding_service or EmbeddingService(self.openai_client)
        self.file_processor = file_processor or FileProcessor()

    def _ensure_vector_service(self):
        """Ensure we have a vector service available, initializing it if needed."""
        if self.vector_service is None:
            self.vector_service = get_vector_service(self.user_id)

    def update_vector_service(self, user_id):
        """Update the vector service if the user has changed."""
        if user_id and (not hasattr(self, 'user_id') or self.user_id != user_id):
            self.user_id = user_id
            self.vector_service = get_vector_service(user_id)

    def create_file_embeddings(self, file: File, chunk_size=None, overlap_size=None) -> int:
        """Process a single file and create embeddings."""
        try:
            self.update_vector_service(file.user.id)

            content = self.file_processor.read_file_content(file)

            user_chunk_size = getattr(file.user, 'chunk_size', CHUNK_SIZE)
            user_overlap_size = getattr(file.user, 'overlap_size', OVERLAP_SIZE)

            try:
                if chunk_size is not None and not isinstance(chunk_size, int):
                    chunk_size = int(chunk_size)
            except (ValueError, TypeError):
                chunk_size = None
            try:
                if overlap_size is not None and not isinstance(overlap_size, int):
                    overlap_size = int(overlap_size)
            except (ValueError, TypeError):
                overlap_size = None

            effective_chunk_size = chunk_size if chunk_size is not None else user_chunk_size
            effective_overlap_size = overlap_size if overlap_size is not None else user_overlap_size
            
            vectors = self._process_chunks(content, file, effective_chunk_size, effective_overlap_size)
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

    def _chunk_text(self, text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP_SIZE) -> List[str]:
        """Split text into smaller chunks with overlap, using RecursiveCharacterTextSplitter.

        This splitter is optimal for generic text, trying to keep paragraphs, sentences,
        and words together as long as possible for better semantic coherence.
        """
        if not text or not isinstance(text, str):
            return []

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            # Default separators: ["\n\n", "\n", " ", ""]
            # separators for languages without word boundaries
            separators=[
                "\n\n",
                "\n",
                " ",
                ".",
                ",",
                "\u200b",  # Zero-width space
                "\uff0c",  # Fullwidth comma
                "\u3001",  # Ideographic comma
                "\uff0e",  # Fullwidth full stop
                "\u3002",  # Ideographic full stop
                ""
            ]
        )

        chunks = text_splitter.split_text(text)
        return chunks

    def _process_chunks(self, content: str, file: File, chunk_size=CHUNK_SIZE, overlap=OVERLAP_SIZE) -> List[Tuple[str, List[float], Dict]]:
        """Process file content into chunks and generate vectors."""
        chunks = self._chunk_text(content, chunk_size=chunk_size, overlap=overlap)
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
                    continue
                except Exception:
                    continue
        except Exception:
            return

    async def _save_workflow_step_snippets(self, snippets_to_save, workflow_run_step_obj):
        """Save retrieved snippets for workflow steps to the database."""
        try:


            successful_saves = 0
            for i, snippet_data in enumerate(snippets_to_save):
                try:
                    file_id = snippet_data["file_id"]
                    file = await database_sync_to_async(File.active_objects.get)(id=file_id)
                    snippet = await database_sync_to_async(WorkflowStepSnippet.active_objects.create)(
                        workflow_run_step=workflow_run_step_obj,
                        file=file,
                        text=snippet_data["text"],
                        similarity_score=snippet_data["similarity_score"],
                        chunk_index=snippet_data["chunk_index"],
                        vector_db_source=snippet_data.get("vector_db_source")
                    )
                    successful_saves += 1
                except File.DoesNotExist:
                    continue
                except Exception:
                    continue
        except Exception:
            return

    async def search_similar_documents(
        self,
        query_text: str,
        file_ids: List[int],
        user_id: int,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        message_obj=None,
        workflow_run_step_obj=None
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
                message_obj,
                workflow_run_step_obj
            )
        except Exception as e:
            return ""

    async def _process_search_results(
        self,
        results: List[Dict],
        similarity_threshold: float,
        message_obj=None,
        workflow_run_step_obj=None
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
            vector_db_source = metadata.get("vector_db_source")

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
                elif workflow_run_step_obj:
                    snippets_to_save.append({
                        "workflow_run_step": workflow_run_step_obj,
                        "file_id": file_id,
                        "text": text,
                        "similarity_score": score,
                        "chunk_index": chunk_index,
                        "vector_db_source": vector_db_source
                    })

        if snippets_to_save and message_obj:
            await self._save_snippets(snippets_to_save, message_obj)
        elif snippets_to_save and workflow_run_step_obj:
            await self._save_workflow_step_snippets(snippets_to_save, workflow_run_step_obj)

        return "\n\n".join(context_parts)

    def query_chunks(
        self,
        query_text: str,
        file_ids: List[int],
        user_id: int,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> List[Dict]:
        """Return normalized retrieval chunks for a query without persisting snippets.

        Used by external runtimes (e.g. SyftBox) that delegate retrieval to Dare.
        Unlike ``search_similar_documents`` this returns structured chunks instead
        of a concatenated string and does not save Snippet records.
        """
        if not file_ids or not query_text:
            return []

        self.update_vector_service(user_id)
        self._ensure_vector_service()

        query_embedding = self.openai_client.create_embeddings(query_text)
        results = self.vector_service.search_documents(
            vector=query_embedding,
            user_id=user_id,
            file_ids=file_ids,
            top_k=top_k,
        )

        chunks: List[Dict] = []
        for match in results:
            score = match.get("score", 0.0)
            if score < similarity_threshold:
                continue
            metadata = match.get("metadata", {})
            text = metadata.get("text", "")
            if not text:
                continue
            chunks.append({
                "text": text,
                "score": score,
                "file_id": metadata.get("file_id", ""),
                "file_name": metadata.get("file_name", "Unknown file"),
                "chunk_index": metadata.get("chunk_index", 0),
            })
        return chunks

    def delete_file_vectors(self, file_id: int, user_id: int) -> bool:
        """Delete all vectors related to a specific file"""
        try:
            return self.vector_service.delete_file_vectors(file_id, user_id)
        except Exception as e:
            raise Exception(f"Error deleting file vectors: {str(e)}")
