# Verified document index publication

Basic and Advanced uploads use the same indexing contract after chunking. Basic text is cut by LangChain's recursive splitter on paragraph, line and sentence boundaries with the configured overlap; Advanced keeps its structural chunking.

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

## Generation lifecycle

An attempt row follows its generation for as long as the file exists:
`running` while the worker holds it, then `published`, `empty`, or `failed`.
When a later generation is published for the same file the previous row
becomes `retired` and `finished_at` is stamped, so the table reads as a history:
a row that is still `published` while the vector database holds nothing for it
is an unexplained loss, not a replacement or a deletion. A `running` row whose
worker stopped becomes `abandoned` (see below), and a new attempt starting on
the same file abandons any older `running` row first. Deleting a file removes
its rows with it.

## Interrupted ingestions

A file only leaves Processing when its job finishes, so a killed worker used to
leave it there forever; the lease on the File row is only reclaimed by the next
explicit reprocess. `core.services.ingestion_reconciliation` asks RQ whether
each Processing file's job is still alive (queued, or started on a worker that
is still heartbeating) and, when it is not, records the interruption: the
journey attempt is failed, `running` attempt rows become `abandoned`, and the
file becomes **Failed** with an "interrupted" message, or **Processed** with a
"previous index retained" note when the interrupted run was a replacement and a
published generation is still active. RQ itself only expires a dead worker's
"started" registry entry after the job's own timeout, which is why a queue
dashboard can show dozens of running jobs with no worker alive.

The sweep runs in three places: every worker runs it once at startup (a
restarted worker is the first to know its predecessor's jobs are dead), the
scheduler runner repeats it every `INGESTION_RECONCILE_INTERVAL_SECONDS`
(default 300; 0 disables), and `python manage.py reconcile_ingestion` runs it
by hand. Worker job failures are also reported to Sentry through the RQ
integration.

## Live index health

Publication verifies a generation once. `GET /api/files/{id}/index-health/`
answers the later question of whether the vectors are still there by listing
the chunk identities stored for the active generation and comparing them with
the file's map rows. The Map tab shows the result and lets the user re-check;
the Files admin has a "Check search index health now" action. Identities only:
the content comparison already happened at publication, and a listing without
vectors or text stays cheap for large documents. A `verified` result means the
index is complete right now; `incomplete` or `missing` means reprocess the
file; `unavailable` means the database could not be reached and says nothing
about the vectors. Apply `files.0027_vectorindexattempt_finished_at` with this
change.

