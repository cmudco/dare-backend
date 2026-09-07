from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from core.services.document_parsing_service import DocumentParsingService
from core.services.dtos.parsed_document_dto import (
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)
from files.constants import FileStatus
from files.models import DocumentChunk, DocumentEntity, DocumentReference, File


def heading(order, text, level, parent=None, number=None, page=1):
    return ParsedElement(
        order=order,
        kind="text",
        label="section_header",
        page_no=page,
        text=text,
        section=text,
        level=level,
        parent_order=parent,
        number=number,
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


ELEMENTS = (
    heading(1, "1 Introduction", 1, number="1"),
    para(2, "Hash tables map keys to slots.", 1),
    heading(3, "2 Collisions", 1, number="2", page=2),
    para(4, "A tombstone marks a deleted slot so probing continues.", 3, page=2),
    heading(5, "3 Open addressing", 1, number="3", page=3),
    para(
        6,
        "Deletion is tricky here; see section 2 for tombstones and Figure 9.",
        5,
        page=3,
    ),
)
PARSED = ParsedDocument(
    text="\n\n".join(e.text for e in ELEMENTS),
    elements=ELEMENTS,
    structure=DocumentStructure(pages=3, sections=3, content_chars=400),
    parser="docling",
)

FLAT_TEXT = (
    "The legacy parser recovers only plain text with no structural markup. "
    "It repeats a short passage several times to reach a realistic paragraph "
    "length for chunking, without any headings, tables, or figures to anchor "
    "structure-aware extraction against. This keeps the fallback path honest."
)
FLAT_PARSED = ParsedDocument(
    text=FLAT_TEXT,
    elements=(),
    structure=DocumentStructure(pages=1, content_chars=400),
    parser="legacy",
)

EMPTY_PARSED = ParsedDocument(
    text="",
    elements=(),
    structure=DocumentStructure(),
    parser="docling",
)


def fake_embeddings(chunks, file_id, user_id, file_name, file_type):
    return [
        (
            f"{file_id}_{index}",
            [0.1, 0.2, 0.3],
            {
                "file_id": str(file_id),
                "user_id": str(user_id),
                "file_name": file_name,
                "file_type": file_type,
                "text": chunk,
                "chunk_index": index,
            },
        )
        for index, chunk in enumerate(chunks)
    ]


def _embedding_stage(file):
    """Pick the embedding stage out of the file's latest journey attempt by key.

    Stage order can shift (a skipped enrichment stage collapses differently
    than a completed one), so tests must not rely on position.
    """
    stages = file.processing_journey["attempts"][-1]["stages"]
    return next(stage for stage in stages if stage["key"] == "embedding")


@contextmanager
def patched_ingestion(
    parsed_document=PARSED, embed_side_effect=fake_embeddings, extra_patchers=()
):
    """Patch the ingestion pipeline's IO boundaries so ``process()`` runs on fakes.

    Parsing persists and returns ``parsed_document``, enrichment is disabled,
    embedding metadata comes from ``embed_side_effect``, and the vector store
    and indexing calls are no-ops. ``extra_patchers`` are additional
    ``unittest.mock.patch(...)`` instances entered alongside the standard
    stack, e.g. to force a resolver failure. The entity predictor is stubbed
    by default to prevent network access; tests may override with their own
    predictor patch provided as an extra patcher.
    """

    def _persist(file):
        DocumentParsingService.persist(file, parsed_document)
        return parsed_document

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "core.services.document_processor.DocumentProcessor.parse_file",
                side_effect=_persist,
            )
        )
        stack.enter_context(
            patch(
                "core.services.document_enrichment_service.DocumentEnrichmentService._enabled",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "core.services.embedding_service.EmbeddingService.create_embeddings_with_metadata",
                side_effect=embed_side_effect,
            )
        )
        stack.enter_context(
            patch(
                "core.services.document_processor.DocumentProcessor.update_vector_service"
            )
        )
        stack.enter_context(
            patch(
                "core.services.document_processor.DocumentProcessor._store_vectors",
                return_value=True,
            )
        )
        stack.enter_context(
            patch(
                "core.services.rag.entity_extractor.NerExtractor._get_predictor",
                return_value=lambda text, labels, threshold: [],
            )
        )
        for extra in extra_patchers:
            stack.enter_context(extra)
        yield


class DocumentIngestionMapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="ingest-map@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="book.pdf",
            file=SimpleUploadedFile("book.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )

    def _process(self):
        return DocumentIngestionService().process(
            DocumentIngestionCommand.from_raw(
                self.file.id, chunk_size=300, overlap_size=40
            )
        )

    def test_ingest_persists_chunks_references_and_indexes(self):
        with patched_ingestion():
            count = self._process()

        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)
        chunks = list(
            DocumentChunk.objects.filter(file=self.file).order_by("chunk_index")
        )
        self.assertEqual(len(chunks), count)
        self.assertEqual(
            [c.section for c in chunks],
            ["1 Introduction", "2 Collisions", "3 Open addressing"],
        )
        self.assertEqual(chunks[1].page_start, 2)

        references = list(
            DocumentReference.objects.filter(file=self.file).order_by("id")
        )
        self.assertEqual(
            [(r.kind, r.key, r.resolved) for r in references],
            [("section", "2", True), ("figure", "9", False)],
        )
        self.assertEqual(references[0].source_chunk.chunk_index, 2)
        self.assertEqual(references[0].target_chunk.chunk_index, 1)
        self.assertEqual(references[0].target_order, 3)

        by_order = {e["order"]: e for e in self.file.document_model["elements"]}
        self.assertEqual(by_order[4]["chunk_index"], 1)
        self.assertEqual(by_order[6]["chunk_index"], 2)

        stage = _embedding_stage(self.file)
        self.assertTrue(stage["details"]["structured"])
        self.assertEqual(stage["details"]["references_found"], 2)
        self.assertEqual(stage["details"]["references_resolved"], 1)

        from files.services.document_map_service import DocumentMapService

        hops = DocumentMapService.load_hops([(str(self.file.id), 2)], self.user.id)
        self.assertEqual([h.chunk_index for h in hops[(str(self.file.id), 2)]], [1])
        self.assertEqual(hops[(str(self.file.id), 2)][0].raw_text, "section 2")
        structure = DocumentMapService.load_structure(
            [(str(self.file.id), 1)], self.user.id
        )
        self.assertEqual(structure[(str(self.file.id), 1)].section, "2 Collisions")

    def test_map_loaders_are_scoped_to_the_owner(self):
        with patched_ingestion():
            self._process()

        from files.services.document_map_service import DocumentMapService

        stranger = get_user_model().objects.create_user(
            email="ingest-map-stranger@example.com", password="pw"
        )
        keys = [(str(self.file.id), 2)]
        self.assertEqual(DocumentMapService.load_hops(keys, stranger.id), {})
        self.assertEqual(
            DocumentMapService.load_structure([(str(self.file.id), 1)], stranger.id), {}
        )
        # The owner still sees them, so the empties above are the scope, not a
        # loader that stopped finding anything.
        self.assertTrue(DocumentMapService.load_hops(keys, self.user.id))

    def test_extraction_failure_keeps_chunks(self):
        with patched_ingestion(
            extra_patchers=[
                patch(
                    "core.services.document_processor.build_references",
                    side_effect=RuntimeError("regex exploded"),
                )
            ]
        ):
            self._process()

        self.assertEqual(DocumentChunk.objects.filter(file=self.file).count(), 3)
        self.assertEqual(DocumentReference.objects.filter(file=self.file).count(), 0)
        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)

        stage = _embedding_stage(self.file)
        self.assertTrue(stage["details"]["structured"])
        self.assertEqual(stage["details"]["references_found"], 0)
        self.assertEqual(stage["details"]["references_resolved"], 0)

    def test_skipped_embedding_keeps_rows_aligned_with_vectors(self):
        """A chunk the embedder drops must not shift the rows after it.

        The fake embedder below mimics ``EmbeddingService`` under a
        per-chunk failure: it advances ``chunk_index`` for every source
        chunk but only returns a vector for the ones that succeeded, so the
        surviving indexes have a gap (0, 2) rather than being consecutive.
        """
        captured_chunk_texts = []

        def embed_dropping_second_chunk(chunks, file_id, user_id, file_name, file_type):
            captured_chunk_texts.extend(chunks)
            return [
                (
                    f"{file_id}_{index}",
                    [0.1, 0.2, 0.3],
                    {
                        "file_id": str(file_id),
                        "user_id": str(user_id),
                        "file_name": file_name,
                        "file_type": file_type,
                        "text": chunk,
                        "chunk_index": index,
                    },
                )
                for index, chunk in enumerate(chunks)
                if index != 1
            ]

        with patched_ingestion(embed_side_effect=embed_dropping_second_chunk):
            self._process()

        rows = list(
            DocumentChunk.objects.filter(file=self.file).order_by("chunk_index")
        )
        self.assertEqual([row.chunk_index for row in rows], [0, 2])
        self.assertEqual(
            rows[1].text,
            "Deletion is tricky here; see section 2 for tombstones and Figure 9.",
        )
        self.assertTrue(captured_chunk_texts[2].endswith(rows[1].text))

        references = list(
            DocumentReference.objects.filter(file=self.file).order_by("id")
        )
        section_ref = next(r for r in references if r.kind == "section")
        self.assertEqual(section_ref.source_chunk.chunk_index, 2)
        self.assertEqual(section_ref.target_order, 3)
        self.assertIsNone(section_ref.target_chunk)
        self.assertTrue(section_ref.resolved)

    def test_small_sections_embed_neighbor_context_but_keep_exact_map_text(self):
        elements = (
            heading(1, "President", 1),
            para(2, ("Alex Morgan " * 7).strip(), 1),
            heading(3, "Historian", 1),
            para(4, ("Abby Hughes " * 7).strip(), 3),
            heading(5, "Secretary", 1),
            para(6, ("William Lane " * 7).strip(), 5),
        )
        parsed = ParsedDocument(
            text="\n\n".join(element.text for element in elements),
            elements=elements,
            structure=DocumentStructure(pages=1, sections=3, content_chars=400),
            parser="docling",
        )
        embedded_texts = []

        def capture_embeddings(chunks, file_id, user_id, file_name, file_type):
            embedded_texts.extend(chunks)
            return fake_embeddings(chunks, file_id, user_id, file_name, file_type)

        with patched_ingestion(
            parsed_document=parsed, embed_side_effect=capture_embeddings
        ):
            self._process()

        rows = list(
            DocumentChunk.objects.filter(file=self.file).order_by("chunk_index")
        )
        historian = next(row for row in rows if row.section == "Historian")
        self.assertEqual(
            historian.text,
            ("Abby Hughes " * 7).strip(),
        )
        self.assertNotIn("President", historian.text)
        self.assertIn("President", embedded_texts[historian.chunk_index])
        self.assertIn("Secretary", embedded_texts[historian.chunk_index])
        self.file.refresh_from_db()
        stage = _embedding_stage(self.file)
        self.assertEqual(stage["details"]["minimum_retrieval_chars"], 200)
        self.assertEqual(stage["details"]["contextualized_chunks"], 3)

    def test_missing_flat_text_is_embedded_but_kept_out_of_document_map(self):
        complete = (
            "Kenneth Walker III won the Super Bowl MVP award. "
            + "Detailed play context " * 22
            + "Making him the first running back to win Super Bowl MVP since "
            "Terrell Davis."
        )
        elements = (
            heading(1, "Walker Steals the Show", 1),
            para(2, complete[:400], 1),
        )
        parsed = ParsedDocument(
            text=complete,
            elements=elements,
            structure=DocumentStructure(pages=1, sections=1, content_chars=500),
            parser="docling",
        )
        embedded_texts = []

        def capture_embeddings(chunks, file_id, user_id, file_name, file_type):
            embedded_texts.extend(chunks)
            return fake_embeddings(chunks, file_id, user_id, file_name, file_type)

        with patched_ingestion(
            parsed_document=parsed, embed_side_effect=capture_embeddings
        ):
            count = DocumentIngestionService().process(
                DocumentIngestionCommand.from_raw(
                    self.file.id, chunk_size=1500, overlap_size=180
                )
            )

        rows = list(DocumentChunk.objects.filter(file=self.file))
        self.assertEqual(count, 2)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("Terrell Davis", rows[0].text)
        self.assertTrue(any("Terrell Davis" in text for text in embedded_texts))

        self.file.refresh_from_db()
        stage = _embedding_stage(self.file)
        self.assertEqual(stage["details"]["recovered_chunks"], 1)
        self.assertGreater(stage["details"]["recovered_characters"], 400)
        self.assertEqual(stage["details"]["chunk_rows"], 1)

    def test_flat_fallback_records_unstructured(self):
        with patched_ingestion(parsed_document=FLAT_PARSED):
            self._process()

        rows = list(
            DocumentChunk.objects.filter(file=self.file).order_by("chunk_index")
        )
        self.assertTrue(rows)
        self.assertTrue(all(row.element_kind == "flat" for row in rows))
        self.assertEqual(DocumentReference.objects.filter(file=self.file).count(), 0)

        self.file.refresh_from_db()
        stage = _embedding_stage(self.file)
        self.assertFalse(stage["details"]["structured"])
        self.assertEqual(stage["details"]["references_found"], 0)

    def test_zero_chunks_persist_nothing_and_report_it(self):
        with patched_ingestion(parsed_document=EMPTY_PARSED):
            count = self._process()

        self.assertEqual(count, 0)
        self.assertFalse(DocumentChunk.objects.filter(file=self.file).exists())

        self.file.refresh_from_db()
        stage = _embedding_stage(self.file)
        self.assertFalse(stage["details"]["structured"])
        # `_resolve_status` falls back to FAILED for a zero-vector result
        # unless every page is a known scan (`page_count` set and
        # `pages_without_text >= page_count`); our empty parse leaves
        # `page_count` unset, so this is the FAILED branch, not NEEDS_OCR.
        self.assertEqual(self.file.status, FileStatus.FAILED)

    def test_nul_in_failure_text_cannot_hide_the_original_ingestion_failure(self):
        with patched_ingestion(embed_side_effect=RuntimeError("bad\x00document")):
            with self.assertRaisesRegex(Exception, "bad document"):
                self._process()

        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.FAILED)
        self.assertEqual(
            self.file.error_message,
            "Error processing file: bad document",
        )
        attempt = self.file.processing_journey["attempts"][-1]
        self.assertEqual(attempt["status"], "failed")
        self.assertNotIn("\x00", attempt["error"])

    def test_reingest_replaces_rows_and_edges(self):
        with patched_ingestion():
            first_count = self._process()

        first_chunks = DocumentChunk.objects.filter(file=self.file).count()
        first_references = DocumentReference.objects.filter(file=self.file).count()
        self.assertEqual(first_chunks, first_count)

        with patched_ingestion():
            second_count = self._process()

        self.assertEqual(second_count, first_count)
        self.assertEqual(
            DocumentChunk.objects.filter(file=self.file).count(), first_chunks
        )
        self.assertEqual(
            DocumentReference.objects.filter(file=self.file).count(),
            first_references,
        )


def fake_ner_predictor(text, labels, threshold):
    if "tombstone" in text.lower():
        return [{"text": "Tombstone Council", "label": "organization", "score": 0.8}]
    return []


class DocumentIngestionEntityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="ingest-ent@example.com", password="pw"
        )
        self.file = File.active_objects.create(
            user=self.user,
            name="book.pdf",
            file=SimpleUploadedFile("book.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )

    def test_ingest_persists_entities_and_reports_lanes(self):
        with patched_ingestion(), patch(
            "core.services.rag.entity_extractor.NerExtractor._get_predictor",
            return_value=fake_ner_predictor,
        ):
            DocumentIngestionService().process(
                DocumentIngestionCommand.from_raw(
                    self.file.id, chunk_size=300, overlap_size=40
                )
            )

        rows = list(DocumentEntity.objects.filter(file=self.file))
        self.assertEqual(
            {(r.kind, r.key) for r in rows}, {("organization", "tombstone council")}
        )
        self.file.refresh_from_db()
        stage = _embedding_stage(self.file)
        # "Tombstone" appears in both the "2 Collisions" and "3 Open
        # addressing" chunks of the module's PARSED fixture, and
        # DocumentMapService.replace_entities writes one row per (chunk,
        # kind, key) by design, so the same entity mentioned in two chunks
        # is two rows, not one.
        self.assertEqual(stage["details"]["entities_found"], 2)
        self.assertEqual(stage["details"]["entity_lanes"], ["identifiers", "ner"])
        self.assertFalse(stage["details"]["entities_error"])

    def test_extraction_failure_never_fails_ingest(self):
        with patch(
            "core.services.document_processor.extract_entities",
            side_effect=RuntimeError("model exploded"),
        ), patched_ingestion():
            DocumentIngestionService().process(
                DocumentIngestionCommand.from_raw(
                    self.file.id, chunk_size=300, overlap_size=40
                )
            )

        self.file.refresh_from_db()
        self.assertEqual(self.file.status, FileStatus.PROCESSED)
        self.assertEqual(DocumentEntity.objects.filter(file=self.file).count(), 0)
        stage = _embedding_stage(self.file)
        self.assertTrue(stage["details"]["entities_error"])
