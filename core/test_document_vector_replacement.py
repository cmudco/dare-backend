from unittest.mock import MagicMock

from django.test import SimpleTestCase

from core.services.document_processor import DocumentProcessor


class DocumentVectorReplacementTests(SimpleTestCase):
    def setUp(self):
        self.processor = object.__new__(DocumentProcessor)
        self.processor.vector_service = MagicMock()

    def test_existing_file_vectors_are_deleted_before_current_vectors_are_stored(self):
        calls = []
        self.processor.vector_service.delete_file_vectors.side_effect = (
            lambda file_id, user_id: calls.append(("delete", file_id, user_id))
        )
        self.processor.vector_service.upsert_vectors.side_effect = (
            lambda vectors, namespace: calls.append(("upsert", len(vectors), namespace))
        )
        vectors = [("file_7_chunk_0", [0.1], {"chunk_index": 0})]

        self.processor._store_vectors(vectors, user_id=3, file_id=7)

        self.assertEqual(
            calls,
            [("delete", 7, 3), ("upsert", 1, "user_3")],
        )

    def test_empty_current_set_still_removes_old_vectors(self):
        self.processor._store_vectors([], user_id=3, file_id=7)

        self.processor.vector_service.delete_file_vectors.assert_called_once_with(7, 3)
        self.processor.vector_service.upsert_vectors.assert_not_called()
