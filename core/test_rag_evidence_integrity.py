from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.services.document_enrichment_service import DocumentEnrichmentService
from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from core.services.document_processor import DocumentProcessor
from core.services.dtos.parsed_document_dto import (
    BoundingBox,
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)
from core.services.llm_helpers.retrieval_targets import TransientRetrievalTarget
from core.services.rag.assembler import ContextAssembler
from core.services.rag.diversifier import MMRDiversifier
from core.services.rag.dtos import (
    CitationCounter,
    ReferenceHop,
    RetrievalRequest,
    RetrievedChunk,
)
from core.services.rag.grounding import GroundingChecker
from core.services.rag.pipeline import RetrievalPipeline
from core.services.rag.reference_resolver import build_references
from core.services.rag.retriever import DocumentRetriever
from core.services.rag.structured_chunker import StructuredChunker
from core.services.vector_service import WeaviateVectorService
from core.test_document_ingestion_map import PARSED, patched_ingestion
from dare_tools.services.retrieval_tool_executor import (
    RetrievalScope,
    RetrievalToolExecutor,
)
from files.models import DocumentChunk, File
from files.tasks import refresh_file_embeddings
from users.models import User


class AgenticRetrievalContractTests(SimpleTestCase):
    def run_search(self, scope, target=None, *, failing_source=None):
        requests = []

        def pipeline_for(source, client):
            def run(request, on_keep):
                requests.append(request)
                if source == failing_source:
                    raise RuntimeError("Provider unavailable")
                chunk = RetrievedChunk(
                    text="Required evidence",
                    source_ref=source,
                    source_type=source,
                    score=0.9,
                )
                blocks = ContextAssembler().assemble(
                    [chunk], on_keep=on_keep, citations=request.citations
                )
                return SimpleNamespace(blocks=blocks, trace=None)

            return SimpleNamespace(run=run)

        with patch(
            "core.services.llm_helpers.semantic_context_helpers.build_pipeline",
            side_effect=pipeline_for,
        ):
            result = async_to_sync(RetrievalToolExecutor().execute)(
                {"query": "Find evidence"}, target, scope
            )
        return result, requests

    def test_calls_actual_advanced_helpers_for_both_sources_and_keeps_payer(self):
        result, requests = self.run_search(
            RetrievalScope(
                embedding_ids=(7,), library_ids=(3,), user_id=1, file_owner_id=9
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["passages_found"], 2)
        self.assertEqual([r.payer_user_id for r in requests], [1, 1])
        self.assertEqual(requests[0].user_id, 9)
        self.assertEqual(requests[0].file_ids, (7,))
        self.assertTrue(result["blocks"][0].startswith("[S1]"))
        self.assertTrue(result["blocks"][1].startswith("[S2]"))

    def test_public_bot_billing_is_passed_to_advanced_search(self):
        result, requests = self.run_search(
            RetrievalScope(embedding_ids=(7,), file_owner_id=9, payer_bot_id=42)
        )
        self.assertTrue(result["success"])
        self.assertIsNone(requests[0].payer_user_id)
        self.assertEqual(requests[0].payer_bot_id, 42)
        self.assertEqual(requests[0].user_id, 9)

    def test_outage_is_not_a_successful_empty_search(self):
        result, _ = self.run_search(
            RetrievalScope(embedding_ids=(7,), user_id=1), failing_source="document"
        )
        self.assertFalse(result["success"])
        self.assertFalse(result["partial"])
        self.assertTrue(result["errors"])

    def test_partial_failure_keeps_evidence_and_exposes_failure(self):
        result, _ = self.run_search(
            RetrievalScope(embedding_ids=(7,), library_ids=(3,), user_id=1),
            failing_source="document",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["passages_found"], 1)

    def test_multiple_agentic_calls_share_citations_and_new_turn_resets_them(self):
        target = TransientRetrievalTarget()
        scope = RetrievalScope(embedding_ids=(7,), user_id=1)
        self.run_search(scope, target)
        result, _ = self.run_search(scope, target)
        self.assertTrue(result["blocks"][0].startswith("[S2]"))
        result, _ = self.run_search(scope, TransientRetrievalTarget())
        self.assertTrue(result["blocks"][0].startswith("[S1]"))


class EvidencePreservationTests(SimpleTestCase):
    def test_real_chunker_preserves_footnote_and_first_paragraph_reference(self):
        elements = (
            ParsedElement(
                1, "text", "section_header", text="1 Terms", level=1, number="1"
            ),
            ParsedElement(
                2,
                "text",
                "text",
                text="See section 2 for cancellation terms.",
                parent_order=1,
            ),
            ParsedElement(
                3,
                "text",
                "text",
                text="Other provisions apply to every customer.",
                parent_order=1,
            ),
            ParsedElement(
                4,
                "text",
                "footnote",
                text="Cancellation is free within fourteen days.",
                parent_order=1,
            ),
            ParsedElement(
                5, "text", "section_header", text="2 Cancellation", level=1, number="2"
            ),
            ParsedElement(
                6,
                "text",
                "text",
                text="Send a written cancellation request.",
                parent_order=5,
            ),
        )
        text = "\n\n".join(e.text for e in elements)
        parsed = ParsedDocument(
            text=text,
            elements=elements,
            structure=DocumentStructure(content_chars=len(text)),
            parser="docling",
        )
        chunks = StructuredChunker(1500, 180).chunk(parsed, fallback_text=text)
        self.assertTrue(any("fourteen days" in c.text for c in chunks))
        refs = build_references(elements, chunks)
        self.assertTrue(
            any(r.kind == "section" and r.key == "2" and r.resolved for r in refs)
        )

    def test_explicit_reference_matches_whole_number(self):
        hop = RetrievedChunk(
            text="Chapter one",
            source_ref="book",
            score=0.1,
            via=ReferenceHop(0, "chapter", "1", "chapter 1"),
        )
        self.assertFalse(RetrievalPipeline._query_names_hop("Explain Chapter 10", hop))
        self.assertTrue(RetrievalPipeline._query_names_hop("Explain Chapter 1", hop))

    def test_uncertainty_is_in_figure_and_page_evidence(self):
        figure = ParsedElement(
            1, "picture", "picture", page_no=1, bbox=BoundingBox(0, 0, 0.5, 0.5)
        )
        parsed = ParsedDocument(
            text="",
            elements=(figure,),
            structure=DocumentStructure(pages=1, pictures=1),
            parser="docling",
        )
        chunker = StructuredChunker(1500, 180)
        model = {
            "elements": [
                {
                    "order": 1,
                    "enrichment": {
                        "status": "complete",
                        "description": "Value is 100",
                        "visible_text": "100",
                        "uncertainty": "Could be 700",
                    },
                }
            ]
        }
        self.assertIn("Could be 700", chunker.chunk(parsed, model)[0].text)
        model = {
            "page_enrichments": [
                {
                    "page_no": 1,
                    "status": "complete",
                    "transcription_markdown": "100",
                    "uncertainty": "Could be 700",
                }
            ]
        }
        self.assertIn("Could be 700", chunker.chunk(parsed, model)[0].text)
        self.assertIn(
            "Could be 700",
            "\n".join(
                DocumentEnrichmentService._page_text_parts(
                    1, model["page_enrichments"][0]
                )
            ),
        )

    def test_rejection_requires_confident_decoration_without_caption(self):
        figure = ParsedElement(
            1,
            "picture",
            "picture",
            page_no=1,
            bbox=BoundingBox(0, 0, 0.01, 0.01),
            classifications=({"label": "logo", "confidence": 0.1},),
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(figure, set()), "describe"
        )
        confident = replace(
            figure, classifications=({"label": "logo", "confidence": 0.99},)
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(confident, set()), "class:logo"
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(
                replace(confident, caption="Figure 2: test results"), set()
            ),
            "describe",
        )
        stamp = replace(
            figure, classifications=({"label": "stamp", "confidence": 0.99},)
        )
        self.assertEqual(
            DocumentEnrichmentService._picture_decision(stamp, set()), "describe"
        )

    def test_document_retrieval_carries_requested_vectors(self):
        service = MagicMock()
        service.search_documents.return_value = [
            {
                "score": 0.9,
                "vector": [1.0, 0.0],
                "metadata": {"file_id": "7", "text": "evidence"},
            }
        ]
        with patch(
            "core.services.vector_service.get_vector_service", return_value=service
        ), patch(
            "core.services.rag.retriever.attach_structure", side_effect=lambda c, u: c
        ):
            chunks = DocumentRetriever(openai_client=MagicMock()).search(
                RetrievalRequest(query="overview", user_id=1, file_ids=(7,)),
                [1.0, 0.0],
                "overview",
                True,
            )
        self.assertEqual(chunks[0].vector, [1.0, 0.0])
        self.assertTrue(service.search_documents.call_args.kwargs["include_vector"])

    def test_grounding_uses_best_score_even_after_diversification(self):
        chunks = [
            RetrievedChunk(text="a", source_ref="a", score=0.1, rerank_score=0.1),
            RetrievedChunk(text="b", source_ref="b", score=0.9, rerank_score=0.9),
        ]
        self.assertTrue(GroundingChecker().check(chunks).answer_found)

    def test_diversification_preserves_reranker_relevance(self):
        vague = RetrievedChunk(
            text="A related topic",
            source_ref="book",
            score=0.99,
            rerank_score=0.1,
            vector=[1.0, 0.0],
        )
        answer = RetrievedChunk(
            text="The actual answer",
            source_ref="book",
            score=0.5,
            rerank_score=0.9,
            vector=[0.5, 0.5],
        )
        picked = MMRDiversifier().diversify([1.0, 0.0], [vague, answer], 1)
        self.assertEqual(picked, [answer])

    @override_settings(RAG_CONTEXT_CHAR_BUDGET=90)
    def test_final_trace_matches_post_budget_context_and_global_citations(self):
        pool = [
            RetrievedChunk(
                text="evidence " + str(i) + " " * 60,
                source_ref="book.pdf",
                score=score,
                rerank_score=score,
                file_id="7",
                source_type="document",
                chunk_index=i,
            )
            for i, score in [(0, 0.8), (1, 0.7), (2, 0.6)]
        ]
        hop = replace(
            pool[0],
            text="discarded hop",
            chunk_index=3,
            rerank_score=0.99,
            via=ReferenceHop(2, "section", "8", "section 8"),
        )
        analyzer = MagicMock(last_error=None)
        analyzer.analyze.return_value = None
        retriever = MagicMock()
        retriever.search.return_value = pool
        retriever.embed.return_value = [1.0, 0.0]
        expander = MagicMock()
        expander.expand.return_value = [hop]
        reranker = MagicMock()
        reranker.rerank.side_effect = lambda q, c, k: sorted(
            c, key=lambda x: x.rerank_score, reverse=True
        )
        reranker.grounding_threshold.return_value = 0.3
        result = RetrievalPipeline(
            retriever, analyzer=analyzer, reranker=reranker, expander=expander
        ).run(
            RetrievalRequest(
                query="Explain",
                file_ids=(7,),
                top_k=2,
                trace=True,
                citations=CitationCounter(4),
            )
        )
        self.assertEqual([c.chunk_index for c in result.chunks], [0])
        self.assertEqual([c.chunk_index for c in result.trace.final], [0])
        self.assertEqual(result.trace.final_size, 1)
        self.assertEqual(result.trace.final[0].citation_id, "S5")
        self.assertTrue(result.blocks[0].startswith("[S5]"))


class IndexReplacementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="index-test@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="book.pdf",
            file="files/book.pdf",
            index_generation="previous",
        )
        self.old_chunk = DocumentChunk.objects.create(
            file=self.file,
            chunk_index=0,
            text="Old reliable evidence",
            element_kind="text",
        )

    def test_failed_indexing_retains_generation_and_map_and_releases_lease(self):
        with patched_ingestion(
            extra_patchers=[
                patch(
                    "core.services.document_processor.DocumentProcessor._store_vectors",
                    side_effect=RuntimeError("outage"),
                )
            ]
        ):
            with self.assertRaisesRegex(Exception, "outage"):
                DocumentIngestionService().process(
                    DocumentIngestionCommand(self.file.pk)
                )
        self.file.refresh_from_db()
        self.assertEqual(self.file.index_generation, "previous")
        self.assertTrue(DocumentChunk.objects.filter(pk=self.old_chunk.pk).exists())
        self.assertIsNone(self.file.ingestion_token)

    def test_success_publishes_generation_with_replacement_map(self):
        with patched_ingestion(), patch(
            "core.services.document_processor.DocumentProcessor._retire_index"
        ) as retire:
            with self.captureOnCommitCallbacks(execute=True):
                count = DocumentIngestionService().process(
                    DocumentIngestionCommand(self.file.pk)
                )
        self.file.refresh_from_db()
        self.assertNotEqual(self.file.index_generation, "previous")
        self.assertEqual(DocumentChunk.objects.filter(file=self.file).count(), count)
        retire.assert_called_once_with("previous", self.user.pk, None)

    def test_duplicate_attempt_does_not_process_or_release_another_lease(self):
        token = uuid4()
        File.active_objects.filter(pk=self.file.pk).update(
            ingestion_token=token, ingestion_started_at=timezone.now()
        )
        with patch.object(DocumentIngestionService, "_process") as process:
            self.assertIsNone(
                DocumentIngestionService().process(
                    DocumentIngestionCommand(self.file.pk)
                )
            )
        process.assert_not_called()
        self.file.refresh_from_db()
        self.assertEqual(self.file.ingestion_token, token)

    def test_search_selects_only_active_generation_and_restores_logical_id(self):
        service = object.__new__(WeaviateVectorService)
        service.client = MagicMock()
        service.client.query_vectors.return_value = [
            {
                "id": "v",
                "score": 0.8,
                "metadata": {"file_id": self.file.vector_index_key, "text": "evidence"},
            }
        ]
        matches = service.search_documents(
            [1.0], self.user.pk, [self.file.pk], include_vector=True
        )
        kwargs = service.client.query_vectors.call_args.kwargs
        args = service.client.query_vectors.call_args.args
        self.assertEqual(args[3]["file_id"]["$in"], [self.file.vector_index_key])
        self.assertEqual(matches[0]["metadata"]["file_id"], str(self.file.pk))
        self.assertTrue(kwargs["include_vector"])

    def test_other_users_file_cannot_be_searched(self):
        other = User.objects.create_user(email="other-index@example.com", password="pw")
        service = object.__new__(WeaviateVectorService)
        service.client = MagicMock()
        self.assertEqual(service.search_documents([1.0], other.pk, [self.file.pk]), [])
        service.client.query_vectors.assert_not_called()

    def test_naive_async_search_reads_the_active_generation(self):
        service = object.__new__(WeaviateVectorService)
        service.client = MagicMock()
        service.client.query_vectors.return_value = [
            {
                "id": "v",
                "score": 0.9,
                "metadata": {
                    "file_id": self.file.vector_index_key,
                    "text": "Old reliable evidence",
                    "file_name": "book.pdf",
                },
            }
        ]
        embeddings = MagicMock()
        embeddings.create_embeddings.return_value = [1.0]
        processor = DocumentProcessor(openai_client=embeddings)
        failures = []
        with patch(
            "core.services.document_processor.get_vector_service", return_value=service
        ):
            result = async_to_sync(processor.search_similar_documents)(
                "evidence", [self.file.pk], self.user.pk, failures=failures
            )
        self.assertEqual(failures, [])
        self.assertIn("[S1] book.pdf", result)
        self.assertIn("Old reliable evidence", result)

    def test_refresh_does_not_delete_previous_index_before_processing(self):
        with patch("files.tasks.delete_file_vectors") as delete, patch(
            "files.tasks.process_file_embeddings"
        ) as process:
            refresh_file_embeddings(self.file.pk, self.user.pk)
        delete.assert_not_called()
        process.assert_called_once()
