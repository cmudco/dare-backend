from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

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
