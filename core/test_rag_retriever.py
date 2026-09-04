from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services.llm_helpers.semantic_context_helpers import run_document_search
from core.services.rag.dtos import RetrievalRequest
from core.services.rag.retriever import DocumentRetriever


class DocumentRetrieverResourceTests(SimpleTestCase):
    def setUp(self):
        self.request = RetrievalRequest(
            query="trees",
            file_ids=(4,),
            user_id=2,
        )

    @patch("core.services.vector_service.get_vector_service")
    def test_closes_vector_service_after_search(self, get_service):
        service = MagicMock()
        service.search_documents.return_value = []
        get_service.return_value = service

        result = DocumentRetriever().search(
            self.request,
            query_vector=[0.1],
            query_text="trees",
            want_vectors=False,
        )

        self.assertEqual(result, [])
        service.close.assert_called_once_with()

    @patch("core.services.vector_service.get_vector_service")
    def test_closes_vector_service_when_search_raises(self, get_service):
        service = MagicMock()
        service.search_documents.side_effect = RuntimeError("search failed")
        get_service.return_value = service

        with self.assertRaisesRegex(RuntimeError, "search failed"):
            DocumentRetriever().search(
                self.request,
                query_vector=[0.1],
                query_text="trees",
                want_vectors=False,
            )

        service.close.assert_called_once_with()

    @patch(
        "core.services.rag.retriever.attach_structure", side_effect=lambda rows, _: rows
    )
    @patch("core.services.vector_service.get_vector_service")
    def test_keeps_source_body_separate_from_retrieval_context(
        self, get_service, _attach_structure
    ):
        service = MagicMock()
        service.search_documents.return_value = [
            {
                "score": 0.9,
                "metadata": {
                    "file_id": "4",
                    "file_name": "chapter.pdf",
                    "chunk_index": 2,
                    "text": "The original paragraph.",
                    "retrieval_text": (
                        "Chapter 1 > Properties\nThe original paragraph."
                    ),
                },
            }
        ]
        get_service.return_value = service

        result = DocumentRetriever().search(
            self.request,
            query_vector=[0.1],
            query_text="properties",
            want_vectors=False,
        )

        self.assertEqual(result[0].text, "The original paragraph.")
        self.assertEqual(
            result[0].searchable_text,
            "Chapter 1 > Properties\nThe original paragraph.",
        )

    @patch("core.services.rag.retriever.attach_structure", side_effect=lambda rows, _: rows)
    @patch("core.services.vector_service.get_vector_service")
    def test_supports_pinecone_metadata_contract(
        self, get_service, _attach_structure
    ):
        service = MagicMock()
        service.search_documents.return_value = [
            {
                "score": 0.9,
                "metadata": {
                    "file_id": "4",
                    "file_name": "chapter.pdf",
                    "chunk_index": 2,
                    "text": "Chapter 1 > Properties\nThe original paragraph.",
                    "body_text": "The original paragraph.",
                },
            }
        ]
        get_service.return_value = service

        result = DocumentRetriever().search(
            self.request,
            query_vector=[0.1],
            query_text="properties",
            want_vectors=False,
        )

        self.assertEqual(result[0].text, "The original paragraph.")
        self.assertEqual(
            result[0].searchable_text,
            "Chapter 1 > Properties\nThe original paragraph.",
        )


class AdvancedDocumentSearchTests(SimpleTestCase):
    @patch("core.services.llm_helpers.semantic_context_helpers.build_pipeline")
    def test_disables_similarity_filter_before_reranking(self, build_pipeline):
        pipeline = MagicMock()
        pipeline.run.return_value = SimpleNamespace(blocks=["context"], trace=None)
        build_pipeline.return_value = pipeline
        processor = MagicMock()

        result = run_document_search(
            processor,
            query="facts from all selected files",
            file_ids=[10, 20, 30],
            vector_user_id=7,
            payer_user_id=9,
            payer_bot_id=None,
            max_context_snippets=6,
            similarity_threshold=0.85,
            target=None,
        )

        request = pipeline.run.call_args.args[0]
        self.assertEqual(result, ["context"])
        self.assertEqual(request.file_ids, (10, 20, 30))
        self.assertEqual(request.user_id, 7)
        self.assertEqual(request.payer_user_id, 9)
        self.assertEqual(request.top_k, 6)
        self.assertEqual(request.similarity_threshold, 0.0)
