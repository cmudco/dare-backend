import logging
from dataclasses import replace
from typing import Dict, List, Optional, Tuple, Union
from uuid import uuid4

from channels.db import database_sync_to_async
from django.db import transaction

from conversations.models import Snippet
from core.config.processing import (
    BATCH_SIZE,
    CHUNK_SIZE,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TOP_K,
    OVERLAP_SIZE,
)
from core.config.vector_db import get_user_namespace
from core.helpers.openai import OpenAIWrapper
from core.services.document_enrichment_service import DocumentEnrichmentService
from core.services.document_parsers.pdf_outline import extract_pdf_outline
from core.services.document_parsing_service import DocumentParsingService
from core.services.document_text_sanitizer import sanitize_document_text
from core.services.dtos.parsed_document_dto import ParsedDocument
from core.services.embedding_service import EmbeddingService
from core.services.file_processing_journey import FileProcessingJourney
from core.services.file_processor import FileProcessor
from core.services.rag.dtos import CitationCounter
from core.services.rag.entity_extractor import extract_entities
from core.services.rag.reference_resolver import build_references
from core.services.rag.structured_chunker import (
    CHUNK_FLAT,
    CHUNK_RECOVERED,
    StructuredChunk,
    StructuredChunker,
)
from core.services.vector_service import get_vector_service
from files.models import File
from files.services.document_map_service import DocumentMapService
from workflows.models import WorkflowStepSnippet

logger = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(
        self,
        openai_client=None,
        vector_service=None,
        embedding_service=None,
        file_processor=None,
        user_id=None,
        parsing_service=None,
        enrichment_service=None,
    ):
        self.openai_client = openai_client or OpenAIWrapper()
        self.user_id = user_id
        self.vector_service = vector_service
        self.embedding_service = embedding_service or EmbeddingService(
            self.openai_client
        )
        self.parsing_service = parsing_service or DocumentParsingService()
        self.enrichment_service = enrichment_service or DocumentEnrichmentService()
        self.file_processor = file_processor or FileProcessor(self.parsing_service)

    def _ensure_vector_service(self):
        """Ensure we have a vector service available, initializing it if needed."""
        if self.vector_service is None:
            self.vector_service = get_vector_service(self.user_id)

    def update_vector_service(self, user_id):
        """Update the vector service if the user has changed."""
        if user_id and (self.vector_service is None or self.user_id != user_id):
            if self.vector_service is not None:
                self.vector_service.close()
            self.user_id = user_id
            self.vector_service = get_vector_service(user_id)

    def create_file_embeddings(
        self,
        file: File,
        chunk_size=None,
        overlap_size=None,
        journey: Optional[FileProcessingJourney] = None,
        parsed: Optional[ParsedDocument] = None,
        ocr_page_limit: Optional[int] = None,
        continue_existing_enrichment: bool = False,
    ) -> int:
        """Process a single file and create embeddings.

        Returns the number of vectors stored. Zero is a legitimate outcome for
        a file that carries no text — an image-only PDF — and the caller is
        responsible for reporting that honestly rather than as success.
        """
        owns_journey = journey is None
        journey = journey or FileProcessingJourney(file)
        if owns_journey:
            journey.begin_attempt()

        try:
            user_chunk_size = getattr(file.user, "chunk_size", CHUNK_SIZE)
            user_overlap_size = getattr(file.user, "overlap_size", OVERLAP_SIZE)

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

            effective_chunk_size = (
                chunk_size if chunk_size is not None else user_chunk_size
            )
            effective_overlap_size = (
                overlap_size if overlap_size is not None else user_overlap_size
            )

            if parsed is None:
                with journey.stage("parsing") as stage:
                    parsed = self.parse_file(file)
                    self._record_parse_details(stage, parsed)

            with journey.stage("enriching") as stage:
                enrichment = self.enrichment_service.enrich(
                    file,
                    parsed,
                    page_limit=ocr_page_limit,
                    continue_existing=continue_existing_enrichment,
                )
                content = enrichment.text
                summary = enrichment.document_model.get("enrichment", {})
                details = {
                    "outcome": summary.get("status", "not_needed"),
                    "model": summary.get("model"),
                    "visual_operations": enrichment.attempted_calls,
                    "provider_requests": enrichment.provider_requests,
                    "cache_hits": enrichment.cache_hits,
                    "described_figures": enrichment.described_figures,
                    "transcribed_pages": enrichment.transcribed_pages,
                    "processed_pages": enrichment.processed_pages,
                    "blank_pages": enrichment.blank_pages,
                    "detected_textless_pages": summary.get("detected_textless_pages"),
                    "selected_textless_pages": summary.get("selected_textless_pages"),
                    "deferred_textless_pages": summary.get("deferred_textless_pages"),
                    "failed_calls": enrichment.failed_calls,
                }
                if summary.get("status") == "not_needed":
                    stage.skip("No visual content required enrichment.", **details)
                elif summary.get("status") in {"partial", "unavailable"}:
                    stage.partial(
                        summary.get("reason")
                        or "Some visual content could not be enriched.",
                        **details,
                    )
                else:
                    stage.add_details(**details)

            generation = uuid4().hex
            staging_key = generation
            previous_key = file.vector_index_key
            previous_backend = file.vector_db_source
            with transaction.atomic():
                current = File.active_objects.select_for_update().get(pk=file.pk)
                if current.ingestion_token != file.ingestion_token:
                    raise RuntimeError("Document ingestion lease was replaced")
                with journey.stage("embedding") as stage:
                    vectors, structure_details = self._embed_with_structure(
                        file,
                        parsed,
                        enrichment.document_model,
                        content,
                        effective_chunk_size,
                        effective_overlap_size,
                    )
                    stage.add_details(
                        text_characters=len(content),
                        chunks=len(vectors),
                        chunk_size=effective_chunk_size,
                        overlap_size=effective_overlap_size,
                        **structure_details,
                    )

                with journey.stage("indexing") as stage:
                    # Connect the vector backend late so its failures blame indexing, not parsing.
                    self.update_vector_service(file.user.id)
                    if vectors:
                        self._store_vectors(vectors, file.user.id, staging_key)
                        file.index_generation = generation
                        file.vector_db_source = file.user.vector_db
                        file.save(
                            update_fields=["index_generation", "vector_db_source"]
                        )
                    backend_name = type(self.vector_service).__name__.removesuffix(
                        "VectorService"
                    )
                    stage.add_details(backend=backend_name, vectors=len(vectors))

            if vectors:
                transaction.on_commit(
                    lambda: self._retire_index(
                        previous_key, file.user.id, previous_backend
                    )
                )

            if owns_journey:
                journey.complete_attempt()
            return len(vectors)
        except Exception as e:
            if owns_journey:
                journey.fail_attempt(e)
            raise Exception(f"Error processing file: {sanitize_document_text(str(e))}")
        finally:
            if self.vector_service is not None:
                self.vector_service.close()
                self.vector_service = None

    def parse_file(self, file: File) -> ParsedDocument:
        """Parse a file and persist its text and document model.

        Exposed on the processor so the ingestion task can inspect the parse —
        page count, pages without text — and decide the file's status without
        parsing the document a second time.
        """
        return self.parsing_service.parse_and_persist(file)

    @staticmethod
    def _record_parse_details(stage, parsed: ParsedDocument) -> None:
        classified_pictures = sum(
            1
            for element in parsed.elements
            if element.kind == "picture" and element.classifications
        )
        stage.add_details(
            parser=parsed.parser,
            fallback_from=parsed.fallback_from,
            fallback_reason=parsed.fallback_reason,
            pages=parsed.structure.pages,
            elements=len(parsed.elements),
            sections=parsed.structure.sections,
            tables=parsed.structure.tables,
            pictures=parsed.structure.pictures,
            classified_pictures=classified_pictures,
            parser_reported_seconds=round(parsed.duration_seconds, 3),
        )

    def create_user_files_embeddings(self, user_id: int) -> bool:
        """Process all files belonging to a specific user"""
        try:
            files = File.active_objects.filter(
                user_id=user_id, is_deleted=False, is_active=True
            )
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

    def _embed_with_structure(
        self,
        file: File,
        parsed: ParsedDocument,
        document_model: Dict,
        content: str,
        chunk_size: int,
        overlap_size: int,
    ) -> Tuple[List[Tuple[str, List[float], Dict]], Dict]:
        """Chunk on structure, embed, and persist the map rows.

        Chunk rows are written even when reference extraction fails, so a
        resolver bug costs edges, never citations.
        """
        chunker = StructuredChunker(chunk_size, overlap_size)
        fallback_text = "\n\n".join(
            part
            for part in (content, parsed.embeddable_text, parsed.recovery_text)
            if part and part.strip()
        )
        structured = chunker.chunk(parsed, document_model, fallback_text=fallback_text)
        vectors = self.embedding_service.create_embeddings_with_metadata(
            [chunk.searchable_text for chunk in structured],
            file.id,
            file.user.id,
            file.name or file.file.name,
            file.file_type,
        )
        if not vectors:
            return [], {
                "structured": False,
                "chunk_rows": 0,
                "references_found": 0,
                "references_resolved": 0,
                "entities_found": 0,
                "entity_lanes": [],
                "entities_error": False,
                "recovered_chunks": 0,
                "recovered_characters": 0,
                "contextualized_chunks": 0,
            }
        vectors = self._attach_source_text(vectors, structured)
        indexed = self._align_chunks(structured, vectors)
        recovered = [
            (index, chunk)
            for index, chunk in indexed
            if chunk.element_kind == CHUNK_RECOVERED
        ]
        mapped = [
            (index, chunk)
            for index, chunk in indexed
            if chunk.element_kind != CHUNK_RECOVERED
        ]
        is_structured = bool(mapped) and mapped[0][1].element_kind != CHUNK_FLAT
        references = []
        if is_structured:
            try:
                positional = build_references(
                    parsed.elements,
                    [chunk for _, chunk in mapped],
                    extract_pdf_outline(file),
                )
                index_of = [index for index, _ in mapped]
                references = [
                    replace(
                        reference,
                        source_chunk_index=index_of[reference.source_chunk_index],
                        target_chunk_index=(
                            index_of[reference.target_chunk_index]
                            if reference.target_chunk_index is not None
                            else None
                        ),
                    )
                    for reference in positional
                ]
            except Exception as error:
                logger.warning(
                    "Reference extraction failed for file %s: %s",
                    file.id,
                    error,
                    exc_info=True,
                )
        found, resolved = DocumentMapService.replace(file, mapped, references)
        DocumentMapService.write_chunk_indexes(file, mapped)

        entities_found, entity_lanes, entities_error = 0, ["identifiers"], False
        try:
            mentions, entity_lanes = extract_entities(
                [chunk.text for _, chunk in mapped]
            )
            entities_found = DocumentMapService.replace_entities(file, mapped, mentions)
        except Exception as error:
            entities_error = True
            logger.warning(
                "Entity extraction failed for file %s: %s",
                file.id,
                error,
                exc_info=True,
            )

        return vectors, {
            "structured": is_structured,
            "minimum_retrieval_chars": chunker.minimum_text_size,
            "contextualized_chunks": sum(
                bool(chunk.retrieval_text) for _, chunk in mapped
            ),
            "recovered_chunks": len(recovered),
            "recovered_characters": sum(len(chunk.text) for _, chunk in recovered),
            "references_found": found,
            "references_resolved": resolved,
            "chunk_rows": len(mapped),
            "entities_found": entities_found,
            "entity_lanes": entity_lanes,
            "entities_error": entities_error,
        }

    @staticmethod
    def _attach_source_text(
        vectors: List[Tuple[str, List[float], Dict]],
        chunks: List[StructuredChunk],
    ) -> List[Tuple[str, List[float], Dict]]:
        """Keep cited source text separate from the contextual embedding input."""
        enriched = []
        for vector_id, embedding, metadata in vectors:
            try:
                chunk = chunks[int(metadata["chunk_index"])]
            except (IndexError, KeyError, TypeError, ValueError):
                enriched.append((vector_id, embedding, metadata))
                continue
            enriched.append(
                (
                    vector_id,
                    embedding,
                    {**metadata, "body_text": chunk.text},
                )
            )
        return enriched

    @staticmethod
    def _align_chunks(
        chunks: List[StructuredChunk], vectors: List[Tuple[str, List[float], Dict]]
    ) -> List[Tuple[int, StructuredChunk]]:
        """Pair each stored vector with its chunk using the index the embedder assigned."""
        indexed: List[Tuple[int, StructuredChunk]] = []
        for _, _, metadata in vectors:
            try:
                position = int(metadata["chunk_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= position < len(chunks):
                indexed.append((position, chunks[position]))
        return indexed

    def _store_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        user_id: int,
        file_id: Union[int, str],
    ) -> bool:
        """Write an isolated generation; the File row publishes it after success."""
        vectors = [
            (f"{vector_id}:{file_id}", embedding, {**metadata, "file_id": str(file_id)})
            for vector_id, embedding, metadata in vectors
        ]
        for i in range(0, len(vectors), BATCH_SIZE):
            batch = vectors[i : i + BATCH_SIZE]
            stored = self.vector_service.upsert_vectors(
                vectors=batch, namespace=get_user_namespace(user_id)
            )
            if stored is False:
                raise RuntimeError("Vector backend rejected the replacement batch")
        return True

    @staticmethod
    def _retire_index(index_key, user_id, backend):
        service = None
        try:
            service = get_vector_service(user_id, backend=backend)
            service.delete_file_vectors(index_key, user_id)
        except Exception:
            # Retired generations are never searched, even when cleanup is unavailable.
            logger.warning(
                "Could not retire document index %s", index_key, exc_info=True
            )
        finally:
            if service is not None:
                service.close()

    async def _save_snippets(self, snippets_to_save, message_obj):
        """Save retrieved snippets to the database."""
        try:
            successful_saves = 0
            for i, snippet_data in enumerate(snippets_to_save):
                try:
                    file_id = snippet_data["file_id"]
                    file = await database_sync_to_async(File.active_objects.get)(
                        id=file_id
                    )
                    snippet = await database_sync_to_async(
                        Snippet.active_objects.create
                    )(
                        message=message_obj,
                        file=file,
                        text=snippet_data["text"],
                        similarity_score=snippet_data["similarity_score"],
                        chunk_index=snippet_data["chunk_index"],
                    )
                    successful_saves += 1
                except File.DoesNotExist:
                    continue
                except Exception:
                    continue
        except Exception:
            return

    async def _save_workflow_step_snippets(
        self, snippets_to_save, workflow_run_step_obj
    ):
        """Save retrieved snippets for workflow steps to the database."""
        try:

            successful_saves = 0
            for i, snippet_data in enumerate(snippets_to_save):
                try:
                    file_id = snippet_data["file_id"]
                    file = await database_sync_to_async(File.active_objects.get)(
                        id=file_id
                    )
                    snippet = await database_sync_to_async(
                        WorkflowStepSnippet.active_objects.create
                    )(
                        workflow_run_step=workflow_run_step_obj,
                        file=file,
                        text=snippet_data["text"],
                        similarity_score=snippet_data["similarity_score"],
                        chunk_index=snippet_data["chunk_index"],
                        vector_db_source=snippet_data.get("vector_db_source"),
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
        workflow_run_step_obj=None,
        failures: Optional[List[str]] = None,
        citations: Optional[CitationCounter] = None,
    ) -> str:
        """Search for similar documents based on the query text.

        A broken vector backend degrades to "no context" rather than failing
        the turn, but the reason is logged and recorded in ``failures`` — an
        unreachable index and a genuinely empty result must not look alike.
        """
        if not file_ids:
            return ""

        try:
            query_embedding = await database_sync_to_async(
                self.openai_client.create_embeddings
            )(query_text)

            results = await database_sync_to_async(self._query_document_vectors)(
                vector=query_embedding,
                user_id=user_id,
                file_ids=file_ids,
                top_k=top_k,
                query_text=query_text,
            )

            return await self._process_search_results(
                results,
                similarity_threshold,
                message_obj,
                workflow_run_step_obj,
                citations,
            )
        except Exception as e:
            logger.warning("Document similarity search failed: %s", e)
            if failures is not None:
                failures.append(f"documents: {e}")
            return ""

    def _query_document_vectors(self, *, user_id: int, **kwargs) -> List[Dict]:
        """Run backend selection and active-generation lookup outside the event loop."""
        self.update_vector_service(user_id)
        return self.vector_service.search_documents(user_id=user_id, **kwargs)

    async def _process_search_results(
        self,
        results: List[Dict],
        similarity_threshold: float,
        message_obj=None,
        workflow_run_step_obj=None,
        citations: Optional[CitationCounter] = None,
    ) -> str:
        """Process search results and collect context."""
        context_parts = []
        snippets_to_save = []

        for match in results:
            score = match.get("score", 0.0)
            if score < similarity_threshold:
                continue

            metadata = match.get("metadata", {})
            text = metadata.get("body_text") or metadata.get("text", "")
            file_id = metadata.get("file_id", "")
            file_name = metadata.get("file_name", "Unknown file")
            chunk_index = metadata.get("chunk_index", 0)
            vector_db_source = metadata.get("vector_db_source")

            if text:
                # Numbered [S#] tag so the model can cite the exact source inline.
                offset = citations.count if citations is not None else 0
                tag = f"S{offset + len(context_parts) + 1}"
                context_parts.append(f"[{tag}] {file_name}:\n{text}")

                if message_obj:
                    snippets_to_save.append(
                        {
                            "message": message_obj,
                            "file_id": file_id,
                            "text": text,
                            "similarity_score": score,
                            "chunk_index": chunk_index,
                        }
                    )
                elif workflow_run_step_obj:
                    snippets_to_save.append(
                        {
                            "workflow_run_step": workflow_run_step_obj,
                            "file_id": file_id,
                            "text": text,
                            "similarity_score": score,
                            "chunk_index": chunk_index,
                            "vector_db_source": vector_db_source,
                        }
                    )

        if snippets_to_save and message_obj:
            await self._save_snippets(snippets_to_save, message_obj)
        elif snippets_to_save and workflow_run_step_obj:
            await self._save_workflow_step_snippets(
                snippets_to_save, workflow_run_step_obj
            )

        if citations is not None:
            citations.count += len(context_parts)
        return "\n\n".join(context_parts)

    def delete_file_vectors(self, file_id: int, user_id: int) -> bool:
        """Delete all vectors related to a specific file"""
        try:
            return self.vector_service.delete_file_vectors(file_id, user_id)
        except Exception as e:
            raise Exception(f"Error deleting file vectors: {str(e)}")
