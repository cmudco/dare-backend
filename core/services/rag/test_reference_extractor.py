from django.test import SimpleTestCase

from core.services.rag.reference_extractor import extract_pointers


class ExtractPointersTests(SimpleTestCase):
    def test_finds_each_kind_in_text_order(self):
        text = (
            "Deletion is tricky; see Section 7.2 for tombstones, as shown in Fig. 7.4 "
            "and Table 2 (cf. Chapter 4, Appendix B, p. 204)."
        )
        found = extract_pointers(text)
        self.assertEqual(
            [(p.kind, p.key) for p in found],
            [
                ("section", "7.2"),
                ("figure", "7.4"),
                ("table", "2"),
                ("chapter", "4"),
                ("appendix", "B"),
                ("page", "204"),
            ],
        )
        self.assertEqual(found[0].raw_text, "Section 7.2")
        self.assertLess(found[0].position, found[1].position)

    def test_dedupes_and_ignores_near_misses(self):
        text = "section 7.2 and again section 7.2; the sections were long; page numbers vary; § 3"
        found = extract_pointers(text)
        self.assertEqual(
            [(p.kind, p.key) for p in found], [("section", "7.2"), ("section", "3")]
        )

    def test_limit_caps_the_result(self):
        text = " ".join(f"see section {i}" for i in range(1, 40))
        self.assertEqual(len(extract_pointers(text, limit=5)), 5)

    def test_statutory_sections_are_not_document_edges(self):
        text = (
            "Title 49 Code of Federal Regulations section 831.4 and "
            "Title 49 United States Code, Section 1154(b) govern this report. "
            "See section 2.1 for the investigation details."
        )
        self.assertEqual(
            [(p.kind, p.key) for p in extract_pointers(text)], [("section", "2.1")]
        )
