from django.test import SimpleTestCase

from core.services.document_parsers.pdf_outline import PdfOutlineTarget
from core.services.dtos.parsed_document_dto import ParsedElement
from core.services.rag.reference_extractor import PointerMatch
from core.services.rag.reference_resolver import ReferenceResolver, build_references
from core.services.rag.structured_chunker import StructuredChunk

ELEMENTS = (
    ParsedElement(
        order=1,
        kind="text",
        label="section_header",
        text="7 Hash tables",
        level=1,
        number="7",
    ),
    ParsedElement(
        order=2,
        kind="text",
        label="section_header",
        text="7.2 Collisions",
        level=2,
        parent_order=1,
        number="7.2",
        page_no=203,
    ),
    ParsedElement(
        order=3,
        kind="text",
        label="text",
        text="A tombstone marks a deleted slot.",
        parent_order=2,
        page_no=204,
    ),
    ParsedElement(
        order=4,
        kind="table",
        label="table",
        caption="Table 1 Load factors",
        table_markdown="| a |\n|---|\n| 1 |",
        parent_order=2,
        page_no=205,
    ),
    ParsedElement(
        order=5,
        kind="text",
        label="section_header",
        text="7.3 Open addressing",
        level=2,
        parent_order=1,
        number="7.3",
        page_no=210,
    ),
    ParsedElement(
        order=6,
        kind="picture",
        label="picture",
        caption="Figure 7.2 Linear probing",
        parent_order=5,
        page_no=211,
    ),
    ParsedElement(
        order=7,
        kind="text",
        label="text",
        text="Deletion is tricky; see section 7.2 and Figure 7.2, Table 1, page 204, Figure 9.",
        parent_order=5,
        page_no=212,
    ),
    ParsedElement(
        order=8,
        kind="text",
        label="section_header",
        text="Appendix B Proofs",
        level=1,
        page_no=300,
    ),
)

CHUNKS = [
    StructuredChunk(
        "7 Hash tables\nintro",
        "text",
        200,
        200,
        1,
        "7 Hash tables",
        ("7 Hash tables",),
        1,
        1,
    ),
    StructuredChunk(
        "7 Hash tables > 7.2 Collisions\nA tombstone marks a deleted slot.",
        "text",
        203,
        204,
        2,
        "7.2 Collisions",
        ("7 Hash tables", "7.2 Collisions"),
        2,
        3,
    ),
    StructuredChunk(
        "7 Hash tables > 7.2 Collisions\nTable: Table 1 Load factors\n\n| a |",
        "table",
        205,
        205,
        2,
        "7.2 Collisions",
        ("7 Hash tables", "7.2 Collisions"),
        4,
        4,
    ),
    StructuredChunk(
        "7 Hash tables > 7.3 Open addressing\nFigure: Figure 7.2 Linear probing\n\n[desc]",
        "figure",
        211,
        211,
        5,
        "7.3 Open addressing",
        ("7 Hash tables", "7.3 Open addressing"),
        6,
        6,
    ),
    StructuredChunk(
        "7 Hash tables > 7.3 Open addressing\nDeletion is tricky; see section 7.2 and Figure 7.2, Table 1, page 204, Figure 9.",
        "text",
        212,
        212,
        5,
        "7.3 Open addressing",
        ("7 Hash tables", "7.3 Open addressing"),
        5,
        7,
    ),
    StructuredChunk(
        "Appendix B Proofs\nproof text",
        "text",
        300,
        300,
        8,
        "Appendix B Proofs",
        ("Appendix B Proofs",),
        8,
        8,
    ),
]


class ReferenceResolverTests(SimpleTestCase):
    def setUp(self):
        self.resolver = ReferenceResolver(ELEMENTS, CHUNKS)

    def test_section_resolves_to_heading_and_first_chunk(self):
        ref = self.resolver.resolve(
            4, PointerMatch("section", "7.2", "see section 7.2", 0)
        )
        self.assertEqual((ref.target_order, ref.target_chunk_index), (2, 1))

    def test_figure_and_table_resolve_through_captions(self):
        figure = self.resolver.resolve(
            4, PointerMatch("figure", "7.2", "Figure 7.2", 0)
        )
        table = self.resolver.resolve(4, PointerMatch("table", "1", "Table 1", 0))
        self.assertEqual(figure.target_chunk_index, 3)
        self.assertEqual(table.target_chunk_index, 2)

    def test_page_and_appendix(self):
        page = self.resolver.resolve(4, PointerMatch("page", "204", "page 204", 0))
        appendix = self.resolver.resolve(
            4, PointerMatch("appendix", "b", "Appendix B", 0)
        )
        self.assertEqual(page.target_chunk_index, 1)
        self.assertEqual((appendix.target_order, appendix.target_chunk_index), (8, 5))

    def test_unknown_target_is_kept_unresolved(self):
        ref = self.resolver.resolve(4, PointerMatch("figure", "9", "Figure 9", 0))
        self.assertIsNotNone(ref)
        self.assertFalse(ref.resolved)

    def test_self_reference_to_own_caption_is_dropped(self):
        self.assertIsNone(
            self.resolver.resolve(3, PointerMatch("figure", "7.2", "Figure 7.2", 0))
        )

    def test_self_reference_to_own_section_is_dropped(self):
        self.assertIsNone(
            self.resolver.resolve(1, PointerMatch("section", "7.2", "section 7.2", 0))
        )

    def test_native_pdf_outline_resolves_chapter_when_docling_heading_is_unnumbered(
        self,
    ):
        resolver = ReferenceResolver(
            ELEMENTS,
            CHUNKS,
            [PdfOutlineTarget("chapter", "3", "Chapter 3: Testing", 210, 1)],
        )
        ref = resolver.resolve(0, PointerMatch("chapter", "3", "Chapter 3", 0))
        self.assertIsNotNone(ref)
        self.assertTrue(ref.resolved)
        self.assertEqual(ref.target_chunk_index, 3)

    def test_build_references_skips_prefix_and_dedupes(self):
        refs = build_references(ELEMENTS, CHUNKS)
        keys = [(r.source_chunk_index, r.kind, r.key) for r in refs]
        self.assertIn((4, "section", "7.2"), keys)
        self.assertIn((4, "figure", "9"), keys)
        self.assertNotIn((3, "figure", "7.2"), keys)
        self.assertEqual(len(keys), len(set(keys)))
        resolved = [r for r in refs if r.resolved]
        self.assertEqual(len(resolved), 4)
