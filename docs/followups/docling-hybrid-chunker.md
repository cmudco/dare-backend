# Follow-up: evaluate Docling's HybridChunker against StructuredChunker

Not scheduled. Written 2026-09-12 after reviewing the Advanced ingestion path.

## Where we stand

Every Docling call in `core/services/document_parsers/docling_parser.py` is the
public API (`DocumentConverter`, `convert`, `iterate_items`, `export_to_markdown`,
`get_image`, `to_top_left_origin`, `meta.classification`). Our own code there is
bookkeeping: projecting Docling items into the persisted element list,
counting structure, and hashing figure crops for the enrichment cache.

Chunking is where we carry logic of our own instead of Docling's:

- `core/services/rag/structured_chunker.py` (`StructuredChunker`) groups elements
  under their heading, splits bodies with LangChain's recursive splitter, and
  keeps `chunk_index` aligned with element order ranges and page ranges so the
  Map tab, references, and citations can point back into the document.
- `DoclingDocumentParser._repair_flat_heading_hierarchy` rebuilds chapter
  nesting when the layout model reports numbered headings as flat siblings.

Docling ships `docling.chunking.HybridChunker`: heading-aware, token-budgeted
(tokenizer-driven, so chunks fit the embedding model), with
`chunk.meta.doc_items` carrying the source items and their page provenance, and
`contextualize()` prefixing heading context the way our `retrieval_text` does.

## What an evaluation must preserve

- Stable `chunk_index` per element order range and page range (the Map, the
  reference resolver and the entity rows all key on it).
- Recovered-text lane (`CHUNK_RECOVERED`) for text Docling missed but native
  PDF extraction found.
- Contextual retrieval text separate from the cited source text (`body_text`).
- Basic mode unchanged: it never has elements, so it stays on the flat LangChain
  path regardless of this decision.

## What it will not fix

Heading flattening happens in the layout model, before any chunker runs;
`HybridChunker` inherits the same flat structure. The repair would sit in front
of it either way, or be replaced by a better layout model
(`docling-layout-egret-*` are the larger options).

## Plan

1. Run both chunkers over `sample_docs/` (the OOP book, the NTSB reports, the
   Abbs Wilkins scans) and compare chunk counts, heading context, and page
   provenance side by side.
2. Re-run the retrieval fixtures in `core/test_rag_evidence_integrity.py` and
   the Map API tests against Hybrid output.
3. If Hybrid wins, keep `StructuredChunker`'s alignment contract as the adapter
   over `chunk.meta.doc_items` rather than rewriting the Map.

Switching chunkers changes chunk identities, so existing files would need a
reprocess to pick it up; the verified-publication path makes that safe.
