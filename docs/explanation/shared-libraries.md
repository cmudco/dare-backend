# Shared libraries

This page explains how DARE imports an external vector corpus and makes it searchable in chat.

## What this feature is

A **shared library** is a curated, externally-sourced dataset that a user can add
to their library and search inside normal DARE chat — alongside their own
documents. The first one is the CMU Civil War pension corpus (~5k transcribed
PDF pages).

The reusable capability underneath: **given an external resource that already holds chunked text
(for example someone else's Weaviate collection), DARE reads its chunks and metadata,
re-vectorizes them into its own vector DB, and exposes the result as a searchable corpus**. Chat
(RAG) uses it now; workflows and research mode could use it later.

## Value proposition

- **No ownership needed.** DARE does not own or re-author the source data; it ingests a copy of
  its **text**, once.
- **First-class knowledge.** The corpus flows through the same retrieval path as an uploaded
  file, so chat citations, snippets and future workflow steps need no special cases.
- **Cheap entitlement.** Access is one row per user, so the same global corpus serves everyone who
  adds it, with no per-user copies of the vectors.

It is a **partial import capability**: it reads a source over plain REST and re-embeds. It is not
a live federation/proxy to the source, and not a generic connector for arbitrary databases (see
[Scope / future](#scope--future)).

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
Re-run only when the source data changes or DARE switches embedding models. The whole
~1.7M-token corpus re-embeds for about $0.22.

```bash
python manage.py import_library --library civil-war-pensions \
  --backend weaviate --source-url https://<host> \
  --source-class CivilWarPensionPage --source-api-key $SOURCE_WEAVIATE_API_KEY
# --dry-run         read + count only (no embedding, no writes)
# --use-source-vectors   trust the source's vectors (ONLY if compat-checked, below)
```

You can also set the source connection in env, which is cleaner for staging/prod dry-runs:

```env
SOURCE_WEAVIATE_URL=https://<source-weaviate-host>
SOURCE_WEAVIATE_CLASS=CivilWarPensionPage
SOURCE_WEAVIATE_API_KEY=<source-api-key>
```

Then run:

```bash
python manage.py import_library --library civil-war-pensions \
  --backend weaviate --dry-run
```

Those are the only source-specific envs. The target DARE vector store still uses
the normal `WEAVIATE_HOST` / `WEAVIATE_PORT` settings, and re-embedding uses the
existing `OPENAI_API_KEY`.

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

Run this check before you trust any external vectors. The importer re-embeds by default because
the label is never the proof.

## Querying (retrieval)

At chat time: the user picks the library in the context picker → the question is
embedded with DARE's embedder → near-vector search against the library's
collection **with no user filter** (it's a global, un-scoped corpus) → top chunks
are blended into the prompt as provenance-tagged context and saved as `Snippet`
citations. Selection persists per conversation (`selected_library_ids`).

## Scope / future

- Currently Weaviate-hosted libraries (the store layer is backend-agnostic;
  Pinecone routing exists but is unused).
- The read path is REST-only because the source's gRPC/GraphQL were not available. REST is enough,
  since only the text is needed.
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
