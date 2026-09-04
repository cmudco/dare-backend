from django.test import SimpleTestCase

from core.services.rag.assembler import ContextAssembler
from core.services.rag.dtos import ReferenceHop, RetrievedChunk


def chunk(index, text, **extra):
    return RetrievedChunk(
        text=text,
        source_ref="book.pdf",
        score=0.9,
        chunk_index=index,
        source_type="document",
        file_id="7",
        file_name="book.pdf",
        **extra
    )


class AssemblerHeaderTests(SimpleTestCase):
    def test_header_carries_page_and_section(self):
        blocks = ContextAssembler().assemble(
            [
                chunk(
                    38,
                    "Deletion…",
                    page_start=212,
                    page_end=212,
                    section="7.3 Open addressing",
                )
            ]
        )
        self.assertTrue(
            blocks[0].startswith("[S1] book.pdf · p. 212 · 7.3 Open addressing:\n")
        )

    def test_page_range_and_missing_structure(self):
        blocks = ContextAssembler().assemble(
            [chunk(1, "x", page_start=204, page_end=206), chunk(2, "y")]
        )
        self.assertTrue(blocks[0].startswith("[S1] book.pdf · pp. 204–206:\n"))
        self.assertTrue(blocks[1].startswith("[S2] book.pdf:\n"))

    def test_expanded_chunk_names_its_source_tag(self):
        source = chunk(
            38, "see section 7.2", page_start=212, section="7.3 Open addressing"
        )
        target = chunk(
            31,
            "A tombstone…",
            page_start=204,
            section="7.2 Collisions",
            via=ReferenceHop(38, "section", "7.2", "see section 7.2"),
        )
        blocks = ContextAssembler().assemble([source, target])
        self.assertIn(
            '[S2] book.pdf · p. 204 · 7.2 Collisions · followed "see section 7.2" from [S1]:',
            blocks[1],
        )

    def test_source_outside_the_kept_set_falls_back_to_chunk_number(self):
        target = chunk(
            31,
            "A tombstone…",
            via=ReferenceHop(38, "section", "7.2", "see section 7.2"),
        )
        blocks = ContextAssembler().assemble([target])
        self.assertIn('followed "see section 7.2" from chunk 38:', blocks[0])

    def test_entity_hop_source_outside_assembled_files_omits_the_origin(self):
        """The entity hop's source lives in another file (``via.source_file_id``);
        if the budget trimmed that file's chunks out of the assembled set
        entirely, a ``chunk <index>`` fallback would name a chunk number from
        a document the model never sees. Unlike a pointer hop, an entity hop
        just drops the origin and cites the shared entity on its own."""
        target = RetrievedChunk(
            text="Affidavit…",
            source_ref="affidavit.pdf",
            score=0.9,
            chunk_index=4,
            source_type="document",
            file_id="8",
            file_name="affidavit.pdf",
            page_start=2,
            section="Affidavit",
            via=ReferenceHop(38, "entity", "wilkins abbs", "Wilkins Abbs", "7"),
        )
        blocks = ContextAssembler().assemble([target])
        self.assertIn('shares "Wilkins Abbs":', blocks[0])
        self.assertNotIn("chunk 38", blocks[0])
        self.assertNotIn(" with ", blocks[0])

    def test_page_end_without_page_start_is_ignored(self):
        blocks = ContextAssembler().assemble([chunk(1, "x", page_end=5)])
        self.assertTrue(blocks[0].startswith("[S1] book.pdf:\n"))

    def test_empty_input_yields_no_blocks(self):
        self.assertEqual(ContextAssembler().assemble([]), [])

    def test_entity_hop_header_says_shares(self):
        source = chunk(38, "Declaration…", section="Declaration")
        target = RetrievedChunk(
            text="Affidavit…",
            source_ref="affidavit.pdf",
            score=0.9,
            chunk_index=4,
            source_type="document",
            file_id="8",
            file_name="affidavit.pdf",
            page_start=2,
            section="Affidavit",
            via=ReferenceHop(38, "entity", "wilkins abbs", "Wilkins Abbs", "7"),
        )
        blocks = ContextAssembler().assemble([source, target])
        self.assertIn(
            '[S2] affidavit.pdf · p. 2 · Affidavit · shares "Wilkins Abbs" with [S1]:',
            blocks[1],
        )
