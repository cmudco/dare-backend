from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services.rag.reference_resolver import ResolvedReference
from core.services.rag.structured_chunker import StructuredChunk
from files.models import DocumentChunk, DocumentReference, File
from files.services.document_map_service import DocumentMapService


def make_file(user, name="book.pdf", document_model=None):
    return File.active_objects.create(
        user=user,
        name=name,
        file=SimpleUploadedFile(name, b"%PDF-test"),
        file_type="application/pdf",
        document_model=document_model or {},
    )


CHUNKS = [
    StructuredChunk(
        text="7 Hash tables > 7.2 Collisions\nA tombstone marks a deleted slot.",
        element_kind="text",
        page_start=204,
        page_end=204,
        section_order=4,
        section="7.2 Collisions",
        heading_path=("7 Hash tables", "7.2 Collisions"),
        order_start=4,
        order_end=5,
    ),
    StructuredChunk(
        text="7 Hash tables > 7.3 Open addressing\nDeletion is tricky; see section 7.2.",
        element_kind="text",
        page_start=212,
        page_end=212,
        section_order=7,
        section="7.3 Open addressing",
        heading_path=("7 Hash tables", "7.3 Open addressing"),
        order_start=7,
        order_end=9,
    ),
]

REFERENCES = [
    ResolvedReference(
        1, "section", "7.2", "see section 7.2", target_order=4, target_chunk_index=0
    ),
    ResolvedReference(1, "figure", "9", "Figure 9"),
    ResolvedReference(1, "figure", "9", "figure 9"),
]


class DocumentMapServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="map-owner@example.com", password="pw"
        )
        self.file = make_file(
            self.user,
            document_model={
                "elements": [
                    {"order": 4, "label": "section_header", "text": "7.2 Collisions"},
                    {"order": 5, "label": "text", "text": "A tombstone…"},
                    {
                        "order": 7,
                        "label": "section_header",
                        "text": "7.3 Open addressing",
                    },
                    {"order": 8, "label": "text", "text": "Deletion is tricky"},
                    {"order": 9, "label": "text", "text": "see section 7.2"},
                    {
                        "order": 12,
                        "label": "text",
                        "text": "never chunked",
                        "chunk_index": 9,
                    },
                ]
            },
        )

    def test_replace_writes_rows_and_counts_resolution(self):
        found, resolved = DocumentMapService.replace(
            self.file, list(enumerate(CHUNKS)), REFERENCES
        )

        self.assertEqual((found, resolved), (2, 1))
        rows = list(
            DocumentChunk.objects.filter(file=self.file).order_by("chunk_index")
        )
        self.assertEqual([row.chunk_index for row in rows], [0, 1])
        self.assertEqual(rows[0].section, "7.2 Collisions")
        self.assertEqual(rows[0].heading_path, ["7 Hash tables", "7.2 Collisions"])
        edges = list(DocumentReference.objects.filter(file=self.file).order_by("id"))
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0].target_chunk, rows[0])
        self.assertEqual(edges[0].target_order, 4)
        self.assertTrue(edges[0].resolved)
        self.assertIsNone(edges[1].target_chunk)
        self.assertFalse(edges[1].resolved)

    def test_replace_is_idempotent(self):
        DocumentMapService.replace(self.file, list(enumerate(CHUNKS)), REFERENCES)
        DocumentMapService.replace(self.file, list(enumerate(CHUNKS[:1])), [])

        self.assertEqual(DocumentChunk.objects.filter(file=self.file).count(), 1)
        self.assertEqual(DocumentReference.objects.filter(file=self.file).count(), 0)

    def test_clear_removes_chunks_and_cascades(self):
        DocumentMapService.replace(self.file, list(enumerate(CHUNKS)), REFERENCES)
        DocumentMapService.clear(self.file.id)

        self.assertFalse(DocumentChunk.objects.filter(file=self.file).exists())
        self.assertFalse(DocumentReference.objects.filter(file=self.file).exists())

    def test_write_chunk_indexes_marks_covered_elements(self):
        DocumentMapService.write_chunk_indexes(self.file, list(enumerate(CHUNKS)))

        self.file.refresh_from_db()
        by_order = {e["order"]: e for e in self.file.document_model["elements"]}
        self.assertEqual(by_order[4]["chunk_index"], 0)
        self.assertEqual(by_order[5]["chunk_index"], 0)
        self.assertEqual(by_order[8]["chunk_index"], 1)
        self.assertNotIn("chunk_index", by_order[12])
