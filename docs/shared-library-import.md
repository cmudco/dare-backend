# Shared Libraries — importing an external vector corpus into DARE

## What this feature is

A **shared library** is a curated, externally-sourced dataset that a user can add
to their library and search inside normal DARE chat — alongside their own
documents. The first one is the CMU Civil War pension corpus (~5k transcribed
PDF pages).

The reusable capability underneath it: **given an external resource that already
holds chunked text (e.g. someone else's Weaviate collection), we can read its
chunks + metadata, re-vectorize them into *our* vector DB, and expose the result
as a searchable corpus** — usable today in chat (RAG), and later in workflows,
research mode, etc.

## Value proposition

- We don't have to own or re-author the source data — we ingest a copy of its
  **text**, once.
- The corpus becomes first-class DARE knowledge: it flows through the exact same
  retrieval path as an uploaded file, so chat citations, snippets, and future
  workflow steps work with zero special-casing.
- Entitlement is per-user and cheap (a row), so the same global corpus serves
  everyone who adds it — no per-user copies of the vectors.

It is a **partial import capability**: it reads a source over plain REST and
re-embeds. It is not (yet) a live federation/proxy to the source, and not a
generic connector for arbitrary databases — see "Scope / future" below.

## Architecture — two stores, one thin link

| Concern | Where it lives |
|---|---|
| Catalog (what it is, shown in UI) + per-user access | **Postgres** |
| The vectors + chunk text + metadata (the searchable corpus) | **Weaviate** |

- `SharedLibrary` (Postgres) is the catalog row the frontend renders. It holds
  *metadata only* — no vectors. It points at the vector container by name
  (`weaviate_class` for Weaviate, `namespace` for Pinecone).
- `UserLibraryAccess` (Postgres) is the "added to my library" link — pure
  entitlement, the thing that makes the corpus show up in the chat context
  picker.
- The Weaviate collection (e.g. `LibraryCivilWarPensions`) holds one object per
  chunk: `{ vector[3072], text, title, source_ref, page, ... }`. Vectors for
  *finding*, text for *reading* — both stored together, which is why citations
  can show the real passage.
- `Snippet` rows (Postgres, written at query time) record which library chunks
  grounded an answer — reusing the same model as document-chunk snippets, with a
  nullable `file` and a `library` FK.

Neither store leaks into the other: Postgres has no vectors, Weaviate has no user
data. The `weaviate_class` name is the only thread between them.

## The import flow (one-time, re-runnable)

`python manage.py import_library` does, per page of ~100 objects:

1. Read objects from the source Weaviate over REST `/v1/objects` (cursor-paged
   via `after`). We take the **text + metadata**; the source's vectors are
   **ignored by default**.
2. **Re-embed the text with DARE's own embedder** (`text-embedding-3-large`) →
   fresh vectors in DARE's space.
3. Map the source's PDF/page fields into our canonical envelope
   (`text` / `title` / `source_ref` + raw fields preserved).
4. Upsert `{vector, text, metadata}` into the library's Weaviate collection
   (deterministic ids → idempotent; the store clears then re-writes).
5. Stamp `object_count` back onto the `SharedLibrary` row.

Embedding happens **once per chunk, here**. Queries only embed the question.
Re-run only when the source data changes or we switch embedding models. Cost is
trivial — the whole ~1.7M-token corpus re-embeds for ~$0.22.

```bash
python manage.py import_library --library civil-war-pensions \
  --backend weaviate --source-url https://<host> \
  --source-class CivilWarPensionPage --source-api-key $SOURCE_WEAVIATE_API_KEY
# --dry-run         read + count only (no embedding, no writes)
# --use-source-vectors   trust the source's vectors (ONLY if compat-checked, below)
```

## The critical lesson: verify embedding-space compatibility

The source's vectors were labelled `text-embedding-3-large / 3072-dim / cosine /
normalized` — identical to ours on paper. They were **a different model behind
the same label**. Querying our `text-embedding-3-large` against their vectors
returned pure noise (~0.04 cosine — the unrelated baseline), because two models
place the same text at unrelated points in space.

**A matching model name + dims is necessary but NOT sufficient.** The only proof
two vector sets share a space is a re-embed-and-compare check:

```text
take a source chunk's text -> embed it with DARE's embedder
cosine(that, the source's stored vector for the same chunk)
  ~0.99  -> same space, safe to use --use-source-vectors
  ~0.04  -> different model, MUST re-embed (the default)
```

Run this gate before trusting any external vectors. We default to re-embedding
precisely because the label is never the proof.

## Querying (retrieval)

At chat time: the user picks the library in the context picker → the question is
embedded with DARE's embedder → near-vector search against the library's
collection **with no user filter** (it's a global, un-scoped corpus) → top chunks
are blended into the prompt as provenance-tagged context and saved as `Snippet`
citations. Selection persists per conversation (`selected_library_ids`).

## Scope / future

- Currently Weaviate-hosted libraries (the store layer is backend-agnostic;
  Pinecone routing exists but is unused).
- Read path is REST-only because the source's gRPC/GraphQL were unavailable to
  us — fine, since we only need the text.
- Natural extensions: surfacing libraries in workflows and research mode, an
  admin UI to register/import a library, and more source connectors beyond
  Weaviate.

## Key files

- `libraries/models.py` — `SharedLibrary`, `UserLibraryAccess`
- `libraries/management/commands/import_library.py` — the importer
- `libraries/services/library_store.py` — backend-agnostic read/write
- `libraries/services/weaviate_library_client.py` — un-scoped Weaviate collection
- `libraries/services/library_search.py` — query + provenance for retrieval
- `core/services/llm_helpers/semantic_context_helpers.py` — blends library
  context + snippets into chat (the `library_ids` path)
