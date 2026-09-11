# Verified document index publication

Basic and Advanced uploads use the same indexing contract after chunking. Basic still uses fixed character windows; Advanced keeps its structural chunking.

1. The complete embedding result must contain exactly one result per expected chunk. IDs, owner, logical file, chunk index, input text, filename and file type must match. Embeddings must have the configured 3,072 dimensions and contain only finite numeric values. Duplicate chunk identities fail; identical vector values for legitimately repeated text are allowed.
2. Writes target a fresh generation. Every write batch must return an explicit acknowledgement. Failed Weaviate batches report how many individual inserts were acknowledged before failure.
3. Verification reads objects directly, including vectors. Weaviate pages through an owner-and-generation filter and checks physical UUIDs and stored original IDs. Pinecone lists the owner's namespace, fetches every page, and selects the generation; this requires a serverless index with the list API. It can cost more for large owner namespaces. There is no ranked search or top-k limit in verification. Backend pagination errors fail the attempt rather than accepting a truncated result.
4. Exact chunk identities, count, persisted metadata (including source and embedding text), dimensions and finite values must match. Visibility mismatches receive at most three reads, with 0.5- and 1-second waits. Transport failures fail closed. Vector values are not compared bit-for-bit because storage may change floating-point precision.
5. Only verified generations become active. The File pointer, map replacement and published attempt record commit together. Failure rolls back the map and pointer, saves failed-attempt evidence outside the rolled-back transaction, and tries to clean only the staged generation. Previous vectors are retired after successful publication.

## Evidence

`VectorIndexAttempt` is available read-only in Django admin under **Vector index attempts**. It records file, owner, backend, generation, status, expected/generated/attempted/acknowledged/verified counts, verification timestamp and elapsed seconds, and a safe failure reason. Successful indexing also includes these details in the existing file processing journey.

The initial attempt row is created before the publication transaction. A killed worker therefore leaves a `running` attempt, not a false success. Counters are finalized on normal completion or handled failure; they are not live progress counters. On a hard kill they may remain zero and must not be interpreted as proof that no writes occurred. Acknowledged counts are lower bounds when a connection fails before the client receives the server's acknowledgement. A `verified_count` is recorded only after the complete generation passes validation. Empty-text outcomes create no active generation and are recorded as `empty`.

## Rollout and compatibility

Apply `files.0026_vectorindexattempt` before restarting the API and all ingestion workers. The table is additive and does not modify existing indexes. Weaviate initialization adds a non-searchable `file_type` property if missing; old objects do not require rewriting. Rolling back application code can leave the additive table/property in place.

Existing uploads are unchanged until explicitly reprocessed. Verification proves consistency at publication time, not indefinite durability. This change does not implement periodic drift detection, bulk repair, or automatic recovery of attempts abandoned by killed workers. It does not establish the cause of historical production data loss.
