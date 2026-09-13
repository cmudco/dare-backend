from unittest.mock import Mock

from django.test import SimpleTestCase

from core.helpers.weaviate import WeaviateClient
from core.services.document_processor import DocumentProcessor


class VectorWriteCountTests(SimpleTestCase):
    def test_success_returns_number_of_acknowledged_vectors(self):
        processor = object.__new__(DocumentProcessor)
        processor.vector_service = Mock()
        processor.vector_service.upsert_vectors.return_value = True
        vectors = [
            (f"file_7_chunk_{i}", [0.1] * 3072, {"chunk_index": i}) for i in range(101)
        ]
        processor.vector_service.read_generation.return_value = [
            {
                "metadata": {"file_id": "generation", "chunk_index": i},
                "vector": [0.1] * 3072,
            }
            for i in range(101)
        ]
        self.assertEqual(processor._store_vectors(vectors, 3, "generation"), 101)
        self.assertEqual(processor.vector_service.upsert_vectors.call_count, 2)

    def test_partial_batch_failure_records_acknowledged_count(self):
        client = object.__new__(WeaviateClient)
        client.upsert_document = Mock(side_effect=[True, RuntimeError("unavailable")])
        vectors = [
            (f"file_7_chunk_{i}", [0.1], {"file_id": "generation"}) for i in range(3)
        ]
        with self.assertLogs("core.helpers.weaviate", level="ERROR") as logs:
            with self.assertRaisesRegex(Exception, "write failed"):
                client.upsert_vectors(vectors, "user_3")
        self.assertIn("attempted=3 acknowledged=1", logs.output[0])

    def test_unacknowledged_write_fails(self):
        client = object.__new__(WeaviateClient)
        client.upsert_document = Mock(return_value=False)
        with self.assertLogs("core.helpers.weaviate", level="ERROR"):
            with self.assertRaisesRegex(Exception, "write failed"):
                client.upsert_vectors(
                    [("file_7_chunk_0", [0.1], {"file_id": "generation"})], "user_3"
                )
