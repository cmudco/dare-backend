# Document ingestion

This page explains how an uploaded file becomes a search index DARE can trust: who may write it,
how a new index is verified before it goes live, what is recorded, and how interrupted runs recover.

## Verified index publication

Basic and Advanced uploads use the same indexing contract after chunking. Basic text is cut by LangChain's recursive splitter on paragraph, line and sentence boundaries with the configured overlap; Advanced keeps its structural chunking.

1. The complete embedding result must contain exactly one result per expected chunk. IDs, owner, logical file, chunk index, input text, filename and file type must match. Embeddings must have the configured 3,072 dimensions and contain only finite numeric values. Duplicate chunk identities fail; identical vector values for legitimately repeated text are allowed.
2. Writes target a fresh generation. Every write batch must return an explicit acknowledgement. Failed Weaviate batches report how many individual inserts were acknowledged before failure.
3. Verification reads objects directly, including vectors. Weaviate pages through an owner-and-generation filter and checks physical UUIDs and stored original IDs. Pinecone lists the owner's namespace, fetches every page, and selects the generation; this requires a serverless index with the list API. It can cost more for large owner namespaces. There is no ranked search or top-k limit in verification. Backend pagination errors fail the attempt rather than accepting a truncated result.
4. Exact chunk identities, count, persisted metadata (including source and embedding text), dimensions and finite values must match. Visibility mismatches receive at most three reads, with 0.5- and 1-second waits. Transport failures fail closed. Vector values are not compared bit-for-bit because storage may change floating-point precision.
5. Only verified generations become active. The File pointer, map replacement and published attempt record commit together. Failure rolls back the map and pointer, saves failed-attempt evidence outside the rolled-back transaction, and tries to clean only the staged generation. Previous vectors are retired after successful publication.

Verification proves consistency at publication time, not lasting durability; [live index
health](#live-index-health) answers the later question. Existing uploads stay as they are until
someone reprocesses them. Weaviate initialization adds a non-searchable `file_type` property if it
is missing; older objects do not need rewriting.

## Ownership and cancellation

Parsing, enrichment and status writes are conditioned on the file's owner and ingestion token. Deletion or replacement of the token cancels the old attempt. Cancellation returns without reporting an ingestion error; the worker log records the file ID. A deleted file's attempt rows are deleted with it.

Embedding, entity extraction, vector upsert and readback verification run outside the publication transaction. Prepared map rows stay in memory until verification succeeds. A short transaction then locks the owned file, replaces its map and publishes the verified generation together. A superseded or deleted attempt cannot publish. Unpublished staged vectors are cleaned up on failure or cancellation; a failed replacement retains the previous map and generation.

Deletion locks the current file before snapshotting its vector generations and backends, including staged attempts. Cleanup is queued after commit using those stable identifiers. Cleanup failures propagate to RQ with three immediate retries, without requiring RQ's separate retry scheduler. Enqueue still depends on Redis availability; durable delivery during a Redis outage would require a transactional outbox.

## Evidence

`VectorIndexAttempt` is available read-only in Django admin under **Vector index attempts**. It records file, owner, backend, generation, status, expected/generated/attempted/acknowledged/verified counts, verification timestamp and elapsed seconds, and a safe failure reason. Successful indexing also includes these details in the existing file processing journey.

The initial attempt row is created before the publication transaction. A killed worker therefore leaves a `running` attempt, not a false success. Counters are finalized on normal completion or handled failure; they are not live progress counters. On a hard kill they may remain zero and must not be interpreted as proof that no writes occurred. Acknowledged counts are lower bounds when a connection fails before the client receives the server's acknowledgement. A `verified_count` is recorded only after the complete generation passes validation. Empty-text outcomes create no active generation and are recorded as `empty`.

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

A file leaves Processing only when its job finishes, and only an explicit reprocess reclaims the
lease on the File row, so a killed worker would leave the file stuck.
`core.services.ingestion_reconciliation` asks RQ whether each Processing file's job is still alive
(queued, or started on a worker that is still heartbeating). When it is not, it records the
interruption:

- the journey attempt is failed;
- `running` attempt rows become `abandoned`;
- the file becomes **Failed** with an "interrupted" message, or **Processed** with a "previous
  index retained" note when the interrupted run was a replacement and a published generation is
  still active.

RQ expires a dead worker's "started" registry entry only after the job's own timeout, which is why
a queue dashboard can show dozens of running jobs with no worker alive.

Reconciliation checks that the observed job ID, token and timestamp still match before marking an
attempt interrupted, so it cannot overwrite an attempt that changed while the sweep was
inspecting RQ.

The sweep runs in three places, none of which needs a new process or a deployment change:

- **Worker startup.** Every worker runs it once when it starts (a restarted worker is the first
  to know its predecessor's jobs are dead).
- **Recurring job.** At the same startup each worker (re)registers a recurring job with
  rq-scheduler, which the running `rqscheduler` process enqueues on the default queue every
  `INGESTION_RECONCILE_INTERVAL_SECONDS` (default 300; 0 disables).
- **By hand.** `python manage.py reconcile_ingestion`.

Registration is idempotent (fixed job id, cancelled and re-added on each worker start). It also
queues one follow-up sweep two minutes later, because a worker that systemd restarts within
seconds of a crash still sees its predecessor listed as alive. Worker job failures also reach
Sentry through the RQ integration.

Detection latency depends on RQ's worker heartbeat: a forking worker (the
default-queue workers) refreshes its key every 30 s with a 90 s TTL while
running a job, so a dead worker is recognised within about two minutes. A
`SimpleWorker` heartbeats once per job with the job's timeout as TTL, so on
that class detection waits for the job timeout.

Keep `rqscheduler` running so the recurring sweep fires. Review interrupted files and failed
cleanup jobs before you retry them selectively; do not replay successful paid work wholesale.

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
about the vectors.

## Vision concurrency

Page transcription and figure description can run in bounded parallel batches. Each operation owns its File instance, provider client, telemetry, and thread database connections. Results retain input order; a cancelled ingestion stops submitting further batches. Async provider clients close within the event loop that used them.

`DOCUMENT_ENRICHMENT_CONCURRENCY` defaults to 2 and is bounded between 1 and 4. Set it to 1 to return to serial execution. This limit applies per document job, so account for the total worker count when sizing provider traffic.

All wallet types use the same concurrency setting. DARE requests retain the estimated-credit check before each paid request. Completed document-vision calls are recorded atomically even if concurrent charges take the balance negative; this does not reserve funds or impose a strict overspend limit across jobs. Other billing callers retain the default insufficient-balance rejection. Cache behavior and provider usage accounting remain part of each operation.

No paid-provider latency or extraction-quality benchmarks exist yet, so there is no measured
production speedup for concurrency above 1.

## Memory per stage

Every finished journey stage records `memory_mb` (the worker process's
resident size at the end of the stage) and `peak_memory_mb` (its high-water
mark). On Linux the peak is reset when an attempt begins, so it is the peak
of that attempt; on macOS it is the process-lifetime peak, which a
long-lived development worker carries over from earlier jobs. They appear in the
Metadata tab and in the stored journey, so a production upload says what each
phase cost on the production box rather than on a developer laptop.

Docling's layout, table and picture models are only needed while parsing, but
they weigh several hundred MB and the enrichment phase that follows can wait
minutes on a vision provider. Ingestion therefore releases the converters
(`release_parser_models`) as soon as the parse is over; a forking worker's
horse exits after the job anyway, and a long-lived worker reloads them on its
next parse (about 20 s). Bulk commands such as `backfill_document_models` keep
the models loaded between files.
