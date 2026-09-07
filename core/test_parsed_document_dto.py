from unittest.mock import patch

from django.test import SimpleTestCase

from core.services.dtos.parsed_document_dto import ParsedDocument, ParsedElement


class ParsedElementStructureKeysTests(SimpleTestCase):
    def test_new_keys_round_trip_through_persistence(self):
        heading = ParsedElement(
            order=3,
            kind="text",
            label="section_header",
            page_no=2,
            text="7.2 Collisions",
            level=2,
            parent_order=1,
            number="7.2",
        )
        body = ParsedElement(
            order=4,
            kind="text",
            label="text",
            page_no=2,
            text="A tombstone…",
            parent_order=3,
            chunk_index=31,
        )
        payload = ParsedDocument(elements=(heading, body), parser="docling").to_dict()

        self.assertEqual(payload["elements"][0]["level"], 2)
        self.assertEqual(payload["elements"][0]["parent_order"], 1)
        self.assertEqual(payload["elements"][0]["number"], "7.2")
        self.assertNotIn("chunk_index", payload["elements"][0])
        self.assertEqual(payload["elements"][1]["chunk_index"], 31)
        self.assertNotIn("level", payload["elements"][1])

        restored = ParsedDocument.from_persisted("", payload)
        self.assertEqual(restored.elements[0].level, 2)
        self.assertEqual(restored.elements[0].parent_order, 1)
        self.assertEqual(restored.elements[0].number, "7.2")
        self.assertEqual(restored.elements[1].chunk_index, 31)
        self.assertIsNone(restored.elements[1].level)

    def test_storage_cap_never_discards_late_structural_anchors(self):
        elements = (
            ParsedElement(1, "text", "text", text="body one"),
            ParsedElement(2, "text", "text", text="body two"),
            ParsedElement(3, "text", "section_header", text="Late chapter"),
            ParsedElement(4, "picture", "picture"),
            ParsedElement(5, "table", "table", table_markdown="| x |"),
            ParsedElement(6, "text", "text", text="discardable late body"),
        )
        with patch("core.services.dtos.parsed_document_dto.MAX_STORED_ELEMENTS", 2):
            payload = ParsedDocument(elements=elements, parser="docling").to_dict()

        self.assertEqual([row["order"] for row in payload["elements"]], [1, 2, 3, 4, 5])
        self.assertEqual(payload["elements_stored"], 5)
        self.assertTrue(payload["elements_truncated"])
        self.assertEqual(
            [row["order"] for row in payload["chunk_elements"]], [1, 2, 3, 4, 5, 6]
        )
        restored = ParsedDocument.from_persisted("", payload)
        self.assertEqual(len(restored.elements), 6)
        self.assertEqual(restored.elements[-1].text, "discardable late body")

    def test_small_document_keeps_lossless_chunk_text(self):
        complete = "A" * 400 + " sentence tail that must remain searchable."
        payload = ParsedDocument(
            elements=(ParsedElement(1, "text", "text", text=complete),),
            parser="docling",
        ).to_dict()

        self.assertEqual(payload["elements"][0]["text"], complete[:400])
        self.assertEqual(payload["chunk_elements"][0]["text"], complete)
        self.assertIs(payload["chunk_elements_lossless"], True)
        restored = ParsedDocument.from_persisted("", payload)
        self.assertEqual(restored.elements[0].text, complete)

    def test_preview_elements_are_not_used_for_reindexing(self):
        payload = {
            "elements": [
                {
                    "order": 1,
                    "kind": "text",
                    "label": "text",
                    "text": "truncated preview",
                }
            ]
        }

        restored = ParsedDocument.from_persisted("", payload)

        self.assertEqual(restored.elements, ())
