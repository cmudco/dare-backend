from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from core.helpers.pinecone import PineconeClient
from core.services.document_processor import DocumentProcessor
from core.services.embedding_service import EmbeddingService
from core.services.vector_integrity import validate_generated, verify_stored
from core.test_document_ingestion_map import (
    FLAT_PARSED,
    fake_embeddings,
    patched_ingestion,
)
from files.models import DocumentChunk, File, VectorIndexAttempt


class ValidationTests(SimpleTestCase):
    def setUp(self):
        self.vectors = fake_embeddings(
            ["first", "second"], 7, 3, "book.txt", "text/plain"
        )

    def test_complete_result_is_valid(self):
        validate_generated(self.vectors, ["first", "second"], 7, 3)

    def test_invalid_results_fail(self):
        cases = [self.vectors[:1], self.vectors + [self.vectors[0]]]
        for embedding in (
            [],
            [1.0],
            [float("nan")] * 3072,
            [float("inf")] * 3072,
            ["1"] * 3072,
            [True] * 3072,
        ):
            cases.append(
                [(self.vectors[0][0], embedding, self.vectors[0][2]), self.vectors[1]]
            )
        for key, value in (
            ("user_id", "4"),
            ("file_id", "8"),
            ("chunk_index", 1),
            ("text", "wrong"),
        ):
            changed = deepcopy(self.vectors)
            changed[0][2][key] = value
            cases.append(changed)
        for vectors in cases:
            with self.subTest(vectors=str(vectors)[:60]), self.assertRaises(ValueError):
                validate_generated(vectors, ["first", "second"], 7, 3)

    def test_equal_count_cannot_hide_duplicates_or_wrong_metadata(self):
        objects = [
            {"metadata": metadata, "vector": values}
            for _, values, metadata in self.vectors
        ]
        verify_stored(self.vectors, objects)
        with self.assertRaises(ValueError):
            verify_stored(self.vectors, [objects[0], objects[0]])
        for key in ("user_id", "file_id", "text", "file_name", "body_text"):
            changed = deepcopy(objects)
            changed[0]["metadata"][key] = "wrong"
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_stored(self.vectors, changed)

    def test_provider_cannot_silently_drop_chunks(self):
        client = Mock()
        client.create_batch_embeddings.return_value = [[0.1] * 3072]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            EmbeddingService(client).create_embeddings_with_metadata(
                ["first", "second"], 7, 3, "book", "text/plain"
            )

    def test_oversized_chunk_fails_instead_of_skipping(self):
        client = Mock()
        service = EmbeddingService(client)
        service.max_tokens_per_request = 1
        with self.assertRaisesRegex(ValueError, "token limit"):
            service.create_embeddings_with_metadata(
                ["one two three four"], 7, 3, "book", "text/plain"
            )
        client.create_batch_embeddings.assert_not_called()


class PublicationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email="integrity@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=user,
            name="book.txt",
            file=SimpleUploadedFile("book.txt", b"text"),
            file_type="text/plain",
            index_generation="old-generation",
            processing_mode="basic",
        )
        DocumentChunk.objects.create(
            file=self.file, chunk_index=0, text="old working chunk"
        )
        self.service = Mock()
        self.written = []

        def upsert(vectors, namespace):
            self.written.extend(vectors)
            return True

        self.service.upsert_vectors.side_effect = upsert
        self.service.read_generation.side_effect = lambda *args: [
            {"metadata": m, "vector": v} for _, v, m in self.written
        ]
        self.processor = DocumentProcessor(
            openai_client=Mock(), vector_service=self.service, user_id=user.pk
        )

    def run_processor(self):
        store = DocumentProcessor._store_vectors
        with patched_ingestion(
            FLAT_PARSED,
            extra_patchers=[
                patch.object(DocumentProcessor, "_store_vectors", new=store)
            ],
        ), patch("core.services.document_processor.time.sleep"):
            return self.processor.create_file_embeddings(
                self.file, chunk_size=150, overlap_size=20, require_vectors=True
            )

    def test_verified_generation_publishes_and_records_evidence(self):
        count = self.run_processor()
        self.file.refresh_from_db()
        attempt = VectorIndexAttempt.objects.get(file=self.file)
        self.assertEqual(self.file.index_generation, attempt.generation)
        self.assertEqual(attempt.status, "published")
        self.assertEqual(
            [
                attempt.expected_count,
                attempt.generated_count,
                attempt.attempted_count,
                attempt.acknowledged_count,
                attempt.verified_count,
            ],
            [count] * 5,
        )
        self.assertIsNotNone(attempt.verified_at)

    def test_readback_failure_retains_old_generation_and_map(self):
        self.service.read_generation.side_effect = lambda *args: []
        with self.assertRaisesRegex(Exception, "count"):
            self.run_processor()
        self.file.refresh_from_db()
        self.assertEqual(self.file.index_generation, "old-generation")
        self.assertEqual(
            list(
                DocumentChunk.objects.filter(file=self.file).values_list(
                    "text", flat=True
                )
            ),
            ["old working chunk"],
        )
        attempt = VectorIndexAttempt.objects.get(file=self.file)
        self.assertEqual(attempt.status, "failed")
        self.assertGreater(attempt.acknowledged_count, 0)
        self.assertEqual(attempt.verified_count, 0)
        self.assertIn("count", attempt.error)
        self.service.delete_file_vectors.assert_called_once_with(
            attempt.generation, self.file.user_id
        )

    def test_partial_write_failure_keeps_acknowledged_evidence(self):
        error = RuntimeError("synthetic write failure")
        error.acknowledged_count = 1
        self.service.upsert_vectors.side_effect = error
        with self.assertRaises(Exception):
            self.run_processor()
        attempt = VectorIndexAttempt.objects.get(file=self.file)
        self.assertEqual(attempt.acknowledged_count, 1)
        self.assertEqual(attempt.verified_count, 0)
        self.file.refresh_from_db()
        self.assertEqual(self.file.index_generation, "old-generation")

    def test_stored_wrong_dimension_never_publishes(self):
        self.service.read_generation.side_effect = lambda *args: [
            {"metadata": m, "vector": [1.0]} for _, _, m in self.written
        ]
        with self.assertRaisesRegex(Exception, "dimension"):
            self.run_processor()
        self.file.refresh_from_db()
        self.assertEqual(self.file.index_generation, "old-generation")
        self.assertEqual(
            VectorIndexAttempt.objects.get(file=self.file).verified_count, 0
        )

    def test_eventual_visibility_is_verified_before_publishing(self):
        calls = []

        def read(*args):
            calls.append(args)
            if len(calls) == 1:
                return []
            return [{"metadata": m, "vector": v} for _, v, m in self.written]

        self.service.read_generation.side_effect = read
        count = self.run_processor()
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            VectorIndexAttempt.objects.get(file=self.file).verified_count, count
        )

    def test_duplicate_delivery_uses_a_new_generation(self):
        self.run_processor()
        previous = self.file.index_generation
        self.written.clear()
        self.processor.vector_service = self.service
        self.run_processor()
        self.assertNotEqual(self.file.index_generation, previous)
        self.assertEqual(
            VectorIndexAttempt.objects.filter(
                file=self.file, status="published"
            ).count(),
            2,
        )


class PineconeReadbackTests(SimpleTestCase):
    def test_owner_namespace_and_all_pages_are_read_with_integer_metadata(self):
        client = object.__new__(PineconeClient)
        client.index = Mock()
        client.index.list.return_value = [["file_7_chunk_0:g"], ["file_7_chunk_1:g"]]
        client.index.fetch.side_effect = [
            SimpleNamespace(
                vectors={
                    f"file_7_chunk_{i}:g": SimpleNamespace(
                        metadata={
                            "file_id": "g",
                            "user_id": "3",
                            "chunk_index": float(i),
                        },
                        values=[0.1] * 3072,
                    )
                }
            )
            for i in range(2)
        ]
        rows = client.read_generation("g", 3, 7)
        client.index.list.assert_called_once_with(namespace="user_3")
        self.assertEqual([r["metadata"]["chunk_index"] for r in rows], [0, 1])
        self.assertTrue(all(type(r["metadata"]["chunk_index"]) is int for r in rows))
        self.assertEqual(client.index.fetch.call_count, 2)

    def test_unexpected_stored_identity_fails(self):
        client = object.__new__(PineconeClient)
        client.index = Mock()
        client.index.list.return_value = [["wrong-id"]]
        client.index.fetch.return_value = SimpleNamespace(
            vectors={
                "wrong-id": SimpleNamespace(
                    metadata={"file_id": "g", "chunk_index": 0}, values=[0.1] * 3072
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            client.read_generation("g", 3, 7)
