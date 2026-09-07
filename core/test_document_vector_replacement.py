from unittest.mock import MagicMock

from django.test import SimpleTestCase

from core.services.document_processor import DocumentProcessor


class DocumentVectorReplacementTests(SimpleTestCase):
    def setUp(self):
        self.processor = object.__new__(DocumentProcessor)
        self.processor.vector_service = MagicMock()

    def test_staging_vectors_never_deletes_the_active_index(self):
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
            [("upsert", 1, "user_3")],
        )

    def test_empty_current_set_preserves_old_vectors(self):
        self.processor._store_vectors([], user_id=3, file_id=7)

        self.processor.vector_service.delete_file_vectors.assert_not_called()
        self.processor.vector_service.upsert_vectors.assert_not_called()

    def test_rejected_batch_is_not_reported_as_success(self):
        self.processor.vector_service.upsert_vectors.return_value = False
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            self.processor._store_vectors(
                [("file_7_chunk_0", [0.1], {"chunk_index": 0})],
                user_id=3,
                file_id="staged-generation",
            )
