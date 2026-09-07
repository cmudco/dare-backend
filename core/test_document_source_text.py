from django.test import SimpleTestCase

from core.services.document_processor import DocumentProcessor
from core.services.rag.structured_chunker import StructuredChunk


class DocumentSourceTextTests(SimpleTestCase):
    def test_vector_metadata_keeps_body_separate_from_embedding_input(self):
        chunks = [
            StructuredChunk(
                text="The original paragraph.",
                retrieval_text="Chapter 1 > Properties\nThe original paragraph.",
                element_kind="text",
            )
        ]
        vectors = [
            (
                "file_7_chunk_0",
                [0.1, 0.2],
                {
                    "chunk_index": 0,
                    "text": chunks[0].searchable_text,
                    "file_id": "7",
                },
            )
        ]

        enriched = DocumentProcessor._attach_source_text(vectors, chunks)

        metadata = enriched[0][2]
        self.assertEqual(
            metadata["text"], "Chapter 1 > Properties\nThe original paragraph."
        )
        self.assertEqual(metadata["body_text"], "The original paragraph.")

    def test_invalid_embedding_index_is_left_unchanged(self):
        metadata = {"chunk_index": 9, "text": "unmatched"}
        vectors = [("file_7_chunk_9", [0.1], metadata)]

        enriched = DocumentProcessor._attach_source_text(vectors, [])

        self.assertIs(enriched[0][2], metadata)
