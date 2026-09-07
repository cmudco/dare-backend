from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.services.rag.entity_extractor import EntityMention
from core.services.rag.reference_resolver import ResolvedReference
from core.services.rag.structured_chunker import StructuredChunk
from files.models import File
from files.services.document_map_service import DocumentMapService


class DocumentMapApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="map-api@example.com", password="pw"
        )
        self.other = get_user_model().objects.create_user(
            email="map-api-other@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="book.pdf",
            file=SimpleUploadedFile("book.pdf", b"%PDF-test"),
            file_type="application/pdf",
            document_model={
                "elements": [
                    {
                        "order": 1,
                        "label": "section_header",
                        "text": "7 Hash tables",
                        "level": 1,
                        "number": "7",
                        "page_no": 198,
                    },
                    {
                        "order": 2,
                        "label": "section_header",
                        "text": "7.2 Collisions",
                        "level": 2,
                        "number": "7.2",
                        "parent_order": 1,
                        "page_no": 203,
                    },
                    {
                        "order": 3,
                        "label": "text",
                        "text": "A tombstone…",
                        "parent_order": 2,
                        "page_no": 204,
                    },
                    {
                        "order": 4,
                        "label": "section_header",
                        "text": "7.3 Open addressing",
                        "level": 2,
                        "number": "7.3",
                        "parent_order": 1,
                        "page_no": 210,
                    },
                    {
                        "order": 5,
                        "label": "text",
                        "text": "see section 7.2",
                        "parent_order": 4,
                        "page_no": 212,
                    },
                ]
            },
        )
        chunks = [
            StructuredChunk(
                "7 Hash tables > 7.2 Collisions\nA tombstone…",
                "text",
                204,
                204,
                2,
                "7.2 Collisions",
                ("7 Hash tables", "7.2 Collisions"),
                2,
                3,
            ),
            StructuredChunk(
                "7 Hash tables > 7.3 Open addressing\nsee section 7.2",
                "text",
                212,
                212,
                4,
                "7.3 Open addressing",
                ("7 Hash tables", "7.3 Open addressing"),
                4,
                5,
            ),
        ]
        self.chunk_texts = [chunk.text for chunk in chunks]
        DocumentMapService.replace(
            self.file,
            list(enumerate(chunks)),
            [
                ResolvedReference(
                    1,
                    "section",
                    "7.2",
                    "see section 7.2",
                    target_order=2,
                    target_chunk_index=0,
                ),
                ResolvedReference(1, "figure", "9", "Figure 9"),
            ],
        )
        DocumentMapService.replace_entities(
            self.file,
            list(enumerate(chunks)),
            [
                [
                    EntityMention("person", "wilkins abbs", "Wilkins Abbs", 3, 0.9),
                    EntityMention("date", "june 26, 1912", "June 26, 1912"),
                ],
                [],
            ],
        )
        self.other_file = File.active_objects.create(
            user=self.user,
            name="affidavit.pdf",
            file=SimpleUploadedFile("affidavit.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(self.other_file, [(0, chunks[0])], [])
        DocumentMapService.replace_entities(
            self.other_file,
            [(0, chunks[0])],
            [[EntityMention("person", "wilkins abbs", "Wilkins Abbs", 1, 0.8)]],
        )

    def test_owner_gets_tree_chunks_and_references(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"/api/files/{self.file.id}/map/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["structured"])
        self.assertEqual(
            body["counts"],
            {
                "sections": 3,
                "chunks": 2,
                "references": 2,
                "resolved": 1,
                "entities": 2,
                "linkedEntities": 1,
            },
        )
        root = body["sections"][0]
        self.assertEqual(root["text"], "7 Hash tables")
        self.assertEqual(
            [child["number"] for child in root["children"]], ["7.2", "7.3"]
        )
        self.assertEqual(root["children"][0]["chunkCount"], 1)
        self.assertEqual(body["chunks"][1]["sectionOrder"], 4)
        self.assertEqual(body["chunks"][1]["charCount"], len(self.chunk_texts[1]))
        self.assertEqual(
            body["chunks"][1]["wordCount"], len(self.chunk_texts[1].split())
        )
        self.assertFalse(body["chunks"][1]["previewTruncated"])
        self.assertNotIn("citedCount", body["chunks"][1])
        self.assertEqual(body["references"][0]["targetChunkIndex"], 0)
        self.assertTrue(body["references"][0]["resolved"])
        self.assertFalse(body["references"][1]["resolved"])

    def test_unstructured_file_reports_sections_only(self):
        DocumentMapService.clear(self.file.id)
        self.client.force_authenticate(user=self.user)
        body = self.client.get(f"/api/files/{self.file.id}/map/").json()
        self.assertFalse(body["structured"])
        self.assertEqual(body["chunks"], [])
        self.assertEqual(len(body["sections"]), 1)

    def test_map_repairs_legacy_flat_chapter_hierarchy(self):
        self.file.document_model = {
            "elements": [
                {
                    "order": 1,
                    "label": "section_header",
                    "text": "Chapter 1",
                    "level": 1,
                },
                {
                    "order": 2,
                    "label": "section_header",
                    "text": "1.5 Multiple Inheritance",
                    "level": 1,
                },
                {
                    "order": 3,
                    "label": "section_header",
                    "text": "Method Resolution Order",
                    "level": 1,
                },
                {
                    "order": 4,
                    "label": "section_header",
                    "text": "1.6 Abstract Base Class",
                    "level": 1,
                },
            ]
        }
        self.file.save(update_fields=["document_model"])
        self.client.force_authenticate(user=self.user)

        body = self.client.get(f"/api/files/{self.file.id}/map/").json()

        root = body["sections"][0]
        self.assertEqual(root["text"], "Chapter 1")
        self.assertEqual(
            [child["text"] for child in root["children"]],
            ["1.5 Multiple Inheritance", "1.6 Abstract Base Class"],
        )
        self.assertEqual(
            root["children"][0]["children"][0]["text"],
            "Method Resolution Order",
        )

    def test_unauthenticated_gets_401(self):
        response = self.client.get(f"/api/files/{self.file.id}/map/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_other_user_gets_404(self):
        self.client.force_authenticate(user=self.other)
        response = self.client.get(f"/api/files/{self.file.id}/map/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_load_full_chunk_text_separately(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"/api/files/{self.file.id}/map/chunks/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["chunkIndex"], 1)
        self.assertEqual(body["text"], self.chunk_texts[1])
        self.assertEqual(body["charCount"], len(self.chunk_texts[1]))
        self.assertEqual(body["wordCount"], len(self.chunk_texts[1].split()))

    def test_missing_or_other_users_chunk_is_hidden(self):
        self.client.force_authenticate(user=self.user)
        missing = self.client.get(f"/api/files/{self.file.id}/map/chunks/99/")
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.other)
        hidden = self.client.get(f"/api/files/{self.file.id}/map/chunks/0/")
        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)

    def test_map_lists_entities_with_cross_document_counts(self):
        self.client.force_authenticate(user=self.user)
        body = self.client.get(f"/api/files/{self.file.id}/map/").json()

        entities = body["chunks"][0]["entities"]
        self.assertEqual(
            [(e["kind"], e["text"], e["otherDocuments"]) for e in entities],
            [("person", "Wilkins Abbs", 1), ("date", "June 26, 1912", 0)],
        )
        self.assertEqual(body["chunks"][1]["entities"], [])
        self.assertEqual(
            (body["counts"]["entities"], body["counts"]["linkedEntities"]), (2, 1)
        )

    def test_linked_entities_matches_by_kind_not_just_key(self):
        """A key that is a person here and an organization elsewhere must
        not count as linked, and its pill must show zero other documents."""
        self.client.force_authenticate(user=self.user)
        chunk = StructuredChunk("Ambiguous Key", "text", 1, 1, 1, "S", ("S",), 1, 1)
        file = File.active_objects.create(
            user=self.user,
            name="kind-mismatch.pdf",
            file=SimpleUploadedFile("kind-mismatch.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(file, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            file,
            [(0, chunk)],
            [[EntityMention("person", "ambiguous key", "Ambiguous Key")]],
        )
        other = File.active_objects.create(
            user=self.user,
            name="kind-mismatch-other.pdf",
            file=SimpleUploadedFile("kind-mismatch-other.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(other, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            other,
            [(0, chunk)],
            [[EntityMention("organization", "ambiguous key", "Ambiguous Key")]],
        )

        body = self.client.get(f"/api/files/{file.id}/map/").json()

        entities = body["chunks"][0]["entities"]
        self.assertEqual(
            [(e["kind"], e["otherDocuments"]) for e in entities], [("person", 0)]
        )
        self.assertEqual(body["counts"]["linkedEntities"], 0)

    def test_date_entity_never_counts_as_linked(self):
        self.client.force_authenticate(user=self.user)
        chunk = StructuredChunk("Date Doc", "text", 1, 1, 1, "S", ("S",), 1, 1)
        file = File.active_objects.create(
            user=self.user,
            name="date-elsewhere.pdf",
            file=SimpleUploadedFile("date-elsewhere.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(file, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            file,
            [(0, chunk)],
            [[EntityMention("date", "june 26, 1912", "June 26, 1912")]],
        )
        other = File.active_objects.create(
            user=self.user,
            name="date-elsewhere-other.pdf",
            file=SimpleUploadedFile("date-elsewhere-other.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(other, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            other,
            [(0, chunk)],
            [[EntityMention("date", "june 26, 1912", "June 26, 1912")]],
        )

        body = self.client.get(f"/api/files/{file.id}/map/").json()

        entities = body["chunks"][0]["entities"]
        self.assertEqual(
            [(e["kind"], e["otherDocuments"]) for e in entities], [("date", 0)]
        )
        self.assertEqual(body["counts"]["linkedEntities"], 0)

    def test_soft_deleted_other_file_does_not_raise_other_documents(self):
        self.client.force_authenticate(user=self.user)
        chunk = StructuredChunk("Person Doc", "text", 1, 1, 1, "S", ("S",), 1, 1)
        file = File.active_objects.create(
            user=self.user,
            name="deleted-link.pdf",
            file=SimpleUploadedFile("deleted-link.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(file, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            file,
            [(0, chunk)],
            [[EntityMention("person", "deleted link person", "Deleted Link Person")]],
        )
        other = File.active_objects.create(
            user=self.user,
            name="deleted-link-other.pdf",
            file=SimpleUploadedFile("deleted-link-other.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        DocumentMapService.replace(other, [(0, chunk)], [])
        DocumentMapService.replace_entities(
            other,
            [(0, chunk)],
            [[EntityMention("person", "deleted link person", "Deleted Link Person")]],
        )
        other.soft_delete()

        body = self.client.get(f"/api/files/{file.id}/map/").json()

        entities = body["chunks"][0]["entities"]
        self.assertEqual(
            [(e["kind"], e["otherDocuments"]) for e in entities], [("person", 0)]
        )
        self.assertEqual(body["counts"]["linkedEntities"], 0)
