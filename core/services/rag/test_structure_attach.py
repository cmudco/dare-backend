from unittest.mock import patch

from django.test import SimpleTestCase

from core.services.rag.dtos import RetrievedChunk
from core.services.rag.retriever import attach_structure
from files.services.document_map_service import ChunkStructure


class AttachStructureTests(SimpleTestCase):
    def test_fills_page_and_section_for_known_chunks(self):
        chunks = [
            RetrievedChunk(
                text="a",
                source_ref="book.pdf",
                score=0.9,
                chunk_index=3,
                source_type="document",
                file_id="7",
            ),
            RetrievedChunk(
                text="b",
                source_ref="book.pdf",
                score=0.8,
                chunk_index=4,
                source_type="document",
                file_id="7",
            ),
            RetrievedChunk(
                text="c",
                source_ref="lib",
                score=0.7,
                chunk_index=1,
                source_type="library",
            ),
        ]
        with patch(
            "files.services.document_map_service.DocumentMapService.load_structure",
            return_value={("7", 3): ChunkStructure(212, 212, "7.3 Open addressing")},
        ) as load:
            attach_structure(chunks, 5)

        load.assert_called_once_with([("7", 3), ("7", 4)], 5)
        self.assertEqual(
            (chunks[0].page_start, chunks[0].section), (212, "7.3 Open addressing")
        )
        self.assertIsNone(chunks[1].page_start)
        self.assertEqual(chunks[2].section, "")

    def test_loader_failure_leaves_chunks_untouched(self):
        chunks = [
            RetrievedChunk(
                text="a",
                source_ref="book.pdf",
                score=0.9,
                chunk_index=3,
                source_type="document",
                file_id="7",
            )
        ]
        with patch(
            "files.services.document_map_service.DocumentMapService.load_structure",
            side_effect=RuntimeError("db down"),
        ):
            self.assertIs(attach_structure(chunks, 5), chunks)
        self.assertIsNone(chunks[0].page_start)

    def test_library_chunks_are_not_looked_up(self):
        chunks = [
            RetrievedChunk(
                text="c",
                source_ref="lib",
                score=0.7,
                chunk_index=1,
                source_type="library",
            )
        ]
        with patch(
            "files.services.document_map_service.DocumentMapService.load_structure",
        ) as load:
            result = attach_structure(chunks, 5)

        load.assert_not_called()
        self.assertIs(result, chunks)
