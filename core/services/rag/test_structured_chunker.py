from django.test import SimpleTestCase

from core.services.dtos.parsed_document_dto import (
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)
from core.services.rag.structured_chunker import CHUNK_RECOVERED, StructuredChunker


def heading(order, text, level, parent=None, page=1):
    return ParsedElement(
        order=order,
        kind="text",
        label="section_header",
        page_no=page,
        text=text,
        section=text,
        level=level,
        parent_order=parent,
    )


def para(order, text, parent, page=1):
    return ParsedElement(
        order=order,
        kind="text",
        label="text",
        page_no=page,
        text=text,
        parent_order=parent,
    )


def doc(elements, parser="docling", pages=5):
    return ParsedDocument(
        text="\n\n".join(e.text for e in elements if e.text),
        elements=tuple(elements),
        structure=DocumentStructure(pages=pages, content_chars=999),
        parser=parser,
    )


class StructuredChunkerTests(SimpleTestCase):
    def setUp(self):
        self.chunker = StructuredChunker(chunk_size=200, overlap=40)

    def test_text_runs_follow_headings_and_carry_structure(self):
        parsed = doc(
            [
                heading(1, "7 Hash tables", 1),
                para(2, "Hash tables map keys to slots.", 1),
                heading(3, "7.2 Collisions", 2, parent=1, page=2),
                para(4, "A tombstone marks a deleted slot.", 3, page=2),
                para(5, "Tombstones are reclaimed on insert.", 3, page=3),
            ]
        )
        chunks = self.chunker.chunk(parsed, {})

        self.assertEqual([c.element_kind for c in chunks], ["text", "text"])
        first, second = chunks
        self.assertEqual(first.text, "Hash tables map keys to slots.")
        self.assertTrue(first.searchable_text.startswith("7 Hash tables\n"))
        self.assertEqual((first.section_order, first.section), (1, "7 Hash tables"))
        self.assertEqual((first.order_start, first.order_end), (1, 2))
        self.assertFalse(second.text.startswith("7 Hash tables"))
        self.assertTrue(
            second.searchable_text.startswith("7 Hash tables > 7.2 Collisions\n")
        )
        self.assertIn("A tombstone marks a deleted slot.", second.text)
        self.assertEqual(second.heading_path, ("7 Hash tables", "7.2 Collisions"))
        self.assertEqual((second.page_start, second.page_end), (2, 3))
        self.assertEqual((second.order_start, second.order_end), (3, 5))

    def test_small_sibling_sections_borrow_same_page_retrieval_context(self):
        chunker = StructuredChunker(chunk_size=600, overlap=60)
        parsed = doc(
            [
                heading(1, "President", 1, page=2),
                para(2, ("Alex Morgan " * 7).strip(), 1, page=2),
                heading(3, "Historian", 1, page=2),
                para(4, ("Abby Hughes " * 7).strip(), 3, page=2),
                heading(5, "Secretary", 1, page=2),
                para(6, ("William Lane " * 7).strip(), 5, page=2),
            ]
        )

        chunks = chunker.chunk(parsed, {})

        historian = next(chunk for chunk in chunks if chunk.section == "Historian")
        self.assertEqual(
            historian.text,
            ("Abby Hughes " * 7).strip(),
        )
        self.assertGreaterEqual(len(historian.searchable_text), 200)
        self.assertTrue(
            historian.searchable_text.startswith(
                "Historian\n" + ("Abby Hughes " * 7).strip()
            )
        )
        self.assertIn("President", historian.searchable_text)
        self.assertIn("Secretary", historian.searchable_text)
        self.assertEqual(historian.page_start, 2)
        self.assertEqual(historian.page_end, 2)
        self.assertEqual(len({chunk.searchable_text for chunk in chunks}), len(chunks))

    def test_small_text_context_does_not_cross_a_table(self):
        table_md = "| role | name |\n|---|---|\n| Historian | Abby Hughes |"
        parsed = doc(
            [
                heading(1, "Before", 1),
                para(2, "Short introduction.", 1),
                ParsedElement(
                    order=3,
                    kind="table",
                    label="table",
                    page_no=1,
                    table_markdown=table_md,
                    parent_order=1,
                ),
                heading(4, "After", 1),
                para(5, "Short conclusion.", 4),
            ]
        )

        chunks = self.chunker.chunk(parsed, {})

        before = next(chunk for chunk in chunks if chunk.section == "Before")
        after = next(chunk for chunk in chunks if chunk.section == "After")
        self.assertEqual(before.text, "Short introduction.")
        self.assertEqual(after.text, "Short conclusion.")
        self.assertEqual(before.searchable_text, "Before\nShort introduction.")
        self.assertEqual(after.searchable_text, "After\nShort conclusion.")

    def test_missing_flat_paragraph_becomes_retrieval_only_recovery(self):
        complete = (
            "Kenneth Walker III won the Super Bowl MVP award. "
            + "Detailed play context " * 22
            + "Making him the first running back to win Super Bowl MVP since "
            "Terrell Davis."
        )
        truncated = complete[:400]
        parsed = ParsedDocument(
            text=complete,
            elements=(
                heading(1, "Walker Steals the Show", 1),
                para(2, truncated, 1),
            ),
            structure=DocumentStructure(pages=1, content_chars=len(complete)),
            parser="docling",
        )

        chunks = StructuredChunker(1500, 180).chunk(parsed, {}, fallback_text=complete)

        mapped = [chunk for chunk in chunks if chunk.element_kind != CHUNK_RECOVERED]
        recovered = [chunk for chunk in chunks if chunk.element_kind == CHUNK_RECOVERED]
        self.assertEqual(len(mapped), 1)
        self.assertNotIn("Terrell Davis", mapped[0].text)
        self.assertEqual(len(recovered), 1)
        self.assertIn("Terrell Davis", recovered[0].text)

    def test_complete_flat_text_does_not_duplicate_structured_content(self):
        complete = "Complete paragraph content " * 30
        parsed = doc([heading(1, "Complete", 1), para(2, complete, 1)])

        chunks = StructuredChunker(1500, 180).chunk(
            parsed, {}, fallback_text=parsed.text
        )

        self.assertFalse(any(chunk.element_kind == CHUNK_RECOVERED for chunk in chunks))

    def test_small_missing_tail_is_not_hidden_by_high_overall_overlap(self):
        missing_fact = "The winning total was exactly 227,000 people."
        visible = "Background context remains searchable. " * 40
        complete = visible + missing_fact
        parsed = ParsedDocument(
            text=complete,
            elements=(
                heading(1, "Impact", 1),
                para(2, visible, 1),
            ),
            structure=DocumentStructure(pages=1, content_chars=len(complete)),
            parser="docling",
        )

        chunks = StructuredChunker(2000, 180).chunk(parsed, {}, fallback_text=complete)

        recovered = [chunk for chunk in chunks if chunk.element_kind == CHUNK_RECOVERED]
        self.assertEqual(len(recovered), 1)
        self.assertIn(missing_fact, recovered[0].text)

    def test_flat_safety_net_does_not_reintroduce_furniture(self):
        header = "Confidential running header " * 8
        body = "Searchable body paragraph " * 15
        parsed = ParsedDocument(
            text=f"{header}\n\n{body}",
            elements=(
                ParsedElement(
                    order=1,
                    kind="text",
                    label="page_header",
                    page_no=1,
                    text=header,
                ),
                ParsedElement(
                    order=2,
                    kind="text",
                    label="text",
                    page_no=1,
                    text=body,
                ),
            ),
            structure=DocumentStructure(pages=1, content_chars=len(body)),
            parser="docling",
        )

        chunks = StructuredChunker(1500, 180).chunk(
            parsed, {}, fallback_text=parsed.text
        )

        self.assertFalse(any(header in chunk.text for chunk in chunks))
        self.assertFalse(any(chunk.element_kind == CHUNK_RECOVERED for chunk in chunks))

    def test_size_limit_splits_a_run_with_overlap(self):
        sentences = [f"Sentence number {i} of the section." for i in range(12)]
        parsed = doc(
            [heading(1, "1 Intro", 1)]
            + [para(2 + i, s, 1) for i, s in enumerate(sentences)]
        )
        chunks = self.chunker.chunk(parsed, {})

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 200)
            self.assertEqual(chunk.section_order, 1)
        tail = chunks[0].text.rsplit(" ", 3)[-1]
        self.assertIn(tail, chunks[1].text)
        self.assertEqual(chunks[0].order_start, 1)
        self.assertEqual(chunks[-1].order_end, 13)

    def test_oversized_paragraph_is_split_but_keeps_its_order(self):
        long_text = "word " * 120
        parsed = doc([heading(1, "1 Intro", 1), para(2, long_text.strip(), 1)])
        chunks = self.chunker.chunk(parsed, {})

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.order_start == 2 and c.order_end == 2 for c in chunks))
        self.assertTrue(all(len(c.text) <= 200 for c in chunks))
        self.assertTrue(all(not c.text.startswith("1 Intro\n") for c in chunks))
        self.assertTrue(all(c.searchable_text.startswith("1 Intro\n") for c in chunks))

    def test_long_heading_path_is_trimmed_to_keep_chunks_within_budget(self):
        h1 = "H1 " + "a" * 62
        h2 = "H2 " + "b" * 62
        h3 = "H3 " + "c" * 62
        h4 = "H4 " + "d" * 62
        paragraphs = [
            para(5 + i, f"Paragraph {i} carries a bit of body text.", 4)
            for i in range(6)
        ]
        parsed = doc(
            [
                heading(1, h1, 1),
                heading(2, h2, 2, parent=1),
                heading(3, h3, 3, parent=2),
                heading(4, h4, 4, parent=3),
            ]
            + paragraphs
        )
        chunks = self.chunker.chunk(parsed, {})

        self.assertTrue(all(len(c.searchable_text) <= 200 for c in chunks))
        self.assertLess(len(chunks), 6)
        for c in chunks:
            first_line = c.searchable_text.split("\n", 1)[0]
            self.assertTrue(first_line.endswith(h4) or first_line.endswith("…"))

    def test_tiny_oversized_table_is_split_not_dropped(self):
        chunker = StructuredChunker(200, 40)
        markdown = "| " + "x" * 2296 + " |"
        parsed = doc(
            [
                heading(1, "1 Data", 1),
                ParsedElement(
                    order=2,
                    kind="table",
                    label="table",
                    page_no=1,
                    table_markdown=markdown,
                    parent_order=1,
                ),
            ]
        )
        chunks = chunker.chunk(parsed, {})

        table_chunks = [c for c in chunks if c.element_kind == "table"]
        self.assertGreaterEqual(len(table_chunks), 1)
        joined = "".join(piece.text for piece in table_chunks)
        self.assertGreaterEqual(joined.count("x"), markdown.count("x"))
        self.assertGreaterEqual(joined.count("|"), markdown.count("|"))
        self.assertTrue(
            all(piece.searchable_text.startswith("1 Data\n") for piece in table_chunks)
        )

    def test_transcribed_page_keeps_its_section(self):
        parsed = doc(
            [
                heading(1, "1 Forms", 1, page=1),
                ParsedElement(
                    order=2,
                    kind="picture",
                    label="picture",
                    page_no=2,
                    parent_order=1,
                ),
                ParsedElement(
                    order=3,
                    kind="picture",
                    label="picture",
                    page_no=2,
                    parent_order=1,
                ),
            ]
        )
        model = {
            "page_enrichments": [
                {
                    "page_no": 2,
                    "status": "complete",
                    "summary": "A form.",
                    "transcription_markdown": "Form text",
                }
            ]
        }
        chunks = self.chunker.chunk(parsed, model)

        page_chunks = [c for c in chunks if c.element_kind == "page_transcription"]
        self.assertEqual(len(page_chunks), 1)
        page = page_chunks[0]
        self.assertEqual(page.section_order, 1)
        self.assertEqual(page.section, "1 Forms")
        self.assertEqual(page.heading_path, ("1 Forms",))

    def test_tables_and_described_figures_are_their_own_chunks(self):
        table_md = "| a | b |\n|---|---|\n| 1 | 2 |"
        parsed = doc(
            [
                heading(1, "2 Results", 1),
                para(2, "As shown in Table 1.", 1),
                ParsedElement(
                    order=3,
                    kind="table",
                    label="table",
                    page_no=1,
                    caption="Table 1 Load factors",
                    table_markdown=table_md,
                    parent_order=1,
                ),
                ParsedElement(
                    order=4,
                    kind="picture",
                    label="picture",
                    page_no=1,
                    caption="Figure 1 Probing",
                    parent_order=1,
                ),
                ParsedElement(
                    order=5, kind="picture", label="picture", page_no=1, parent_order=1
                ),
                para(6, "Closing remark.", 1),
            ]
        )
        model = {
            "elements": [
                {
                    "order": 4,
                    "enrichment": {
                        "status": "complete",
                        "description": "An array of 16 slots.",
                        "visible_text": "slot 5",
                    },
                },
                {
                    "order": 5,
                    "enrichment": {"status": "skipped", "reason": "small_picture"},
                },
            ]
        }
        chunks = self.chunker.chunk(parsed, model)

        kinds = [c.element_kind for c in chunks]
        self.assertEqual(kinds, ["text", "table", "figure", "text"])
        self.assertIn("Table 1 Load factors", chunks[1].text)
        self.assertIn("| a | b |", chunks[1].text)
        self.assertEqual((chunks[1].order_start, chunks[1].order_end), (3, 3))
        self.assertIn("Figure 1 Probing", chunks[2].text)
        self.assertIn("An array of 16 slots.", chunks[2].text)
        self.assertIn("slot 5", chunks[2].text)
        self.assertEqual(chunks[3].order_start, 6)

    def test_transcribed_pages_replace_their_elements(self):
        parsed = doc(
            [
                ParsedElement(order=1, kind="picture", label="picture", page_no=1),
                ParsedElement(order=2, kind="picture", label="picture", page_no=1),
                heading(3, "Notes", 1, page=2),
                para(4, "Typed page.", 3, page=2),
            ]
        )
        model = {
            "page_enrichments": [
                {
                    "page_no": 1,
                    "status": "complete",
                    "summary": "A pension form.",
                    "transcription_markdown": "Declaration for Invalid Pension",
                }
            ]
        }
        chunks = self.chunker.chunk(parsed, model)

        self.assertEqual(
            [c.element_kind for c in chunks], ["page_transcription", "text"]
        )
        page = chunks[0]
        self.assertIn("Declaration for Invalid Pension", page.text)
        self.assertIn("A pension form.", page.text)
        self.assertEqual((page.page_start, page.page_end), (1, 1))
        self.assertEqual((page.order_start, page.order_end), (1, 2))
        self.assertNotIn("Page 1", page.text)

    def test_scan_only_document_keeps_page_metadata_without_docling_elements(self):
        parsed = ParsedDocument(
            text="", elements=(), parser="docling", structure=DocumentStructure(pages=1)
        )
        model = {
            "page_enrichments": [
                {
                    "page_no": 1,
                    "status": "complete",
                    "kind": "page_transcription",
                    "transcription_markdown": "A pension application.",
                }
            ]
        }

        chunks = self.chunker.chunk(parsed, model)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].element_kind, "page_transcription")
        self.assertEqual((chunks[0].page_start, chunks[0].page_end), (1, 1))
        self.assertNotIn("Page 1", chunks[0].text)

    def test_blank_page_result_creates_no_searchable_chunk(self):
        parsed = ParsedDocument(
            text="", elements=(), parser="docling", structure=DocumentStructure(pages=1)
        )
        model = {
            "page_enrichments": [
                {"page_no": 1, "status": "complete", "kind": "blank_page"}
            ]
        }
        self.assertEqual(self.chunker.chunk(parsed, model), [])

    def test_table_chunks_respect_configured_size_and_repeat_header(self):
        rows = [f"| row {i} | {'value ' * 8}|" for i in range(20)]
        markdown = "| key | value |\n|---|---|\n" + "\n".join(rows)
        parsed = doc(
            [
                heading(1, "1 Data", 1),
                ParsedElement(
                    order=2,
                    kind="table",
                    label="table",
                    page_no=1,
                    table_markdown=markdown,
                    parent_order=1,
                ),
            ]
        )

        chunks = self.chunker.chunk(parsed, {})
        tables = [chunk for chunk in chunks if chunk.element_kind == "table"]

        self.assertGreater(len(tables), 1)
        self.assertTrue(all(len(chunk.text) <= 200 for chunk in tables))
        self.assertTrue(all("| key | value |" in chunk.text for chunk in tables))

    def test_furniture_is_dropped(self):
        parsed = doc(
            [
                heading(1, "1 Intro", 1),
                ParsedElement(
                    order=2,
                    kind="text",
                    label="page_header",
                    page_no=1,
                    text="Running head",
                    parent_order=1,
                ),
                para(3, "Body.", 1),
            ]
        )
        chunks = self.chunker.chunk(parsed, {})
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("Running head", chunks[0].text)

    def test_no_elements_falls_back_to_flat_chunks(self):
        parsed = ParsedDocument(text="", elements=(), parser="legacy")
        chunks = self.chunker.chunk(parsed, {}, fallback_text="alpha " * 100)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.element_kind == "flat" for c in chunks))
        self.assertIsNone(chunks[0].page_start)
        self.assertEqual(chunks[0].heading_path, ())
