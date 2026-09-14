# Document ingestion lifecycle

The default queue and startup/periodic reconciliation remain in use. There are no schema migrations for this change.

## Connection ownership

`InspectableWorker` closes Django database connections after its startup sweep, on success and failure, and immediately before each fork. The child opens its own PostgreSQL connection. Never open and retain a parent-side database connection across a job fork. HTTP connection health checks do not enforce process isolation.

## Cancellation and publication

Parsing, enrichment and status writes are conditioned on the file's owner and ingestion token. Deletion or replacement of that token cancels the old attempt. Cancellation returns without reporting an ingestion error; the worker log records the file ID. A deleted file's attempt rows are deleted with it.

Embedding, entity extraction, vector upsert and readback verification run outside the publication transaction. Prepared map rows are held in memory until verification succeeds. A short transaction then locks the owned file, replaces its map and publishes the verified generation together. A superseded/deleted attempt cannot publish. Unpublished staged vectors are cleaned up on failure/cancellation; a failed replacement retains the previous map and generation.

Deletion locks the current file before snapshotting its vector generations and backends, including staged attempts. Cleanup is queued after commit using those stable identifiers, and jobs created before this change remain supported. Cleanup failures propagate to RQ with three immediate retries; this does not require RQ's separate retry scheduler. The enqueue itself still depends on Redis being available. Durable delivery during a Redis outage would require a transactional outbox.

Reconciliation checks that the observed job ID, token and timestamp still match before marking an attempt interrupted. It cannot overwrite an attempt that changed while the sweep was inspecting RQ.

## Vision concurrency

`DOCUMENT_ENRICHMENT_CONCURRENCY` defaults to 2 and is bounded to 1–4 operations per document. Set it to 1 to run all vision operations serially. This is a per-document limit; total concurrency also depends on active worker count.

External-key calls can run concurrently in bounded batches. Each operation uses its own File instance, provider client, telemetry and thread-local database connection. Results are merged in document order. All work in the current batch is allowed to finish and record usage before cancellation exits; later batches are not submitted. Native PDF rendering retains its existing process-wide PDFium lock. Provider clients close in the event loop that performed the request.

DARE-funded calls remain serial because credit checks do not reserve funds for concurrent requests. Parallelizing that lane requires billing reservations. The selected models, OCR approval/page limits, figure limits, cache scope and bounded structured-output retries are unchanged.

## Rollout

Deploy the backend and restart all workers using `InspectableWorker`, including workers consuming non-document queues. Existing worker processes retain the old code and potentially unsafe database connections. Keep the scheduler running. No new queue, worker count, database setting or model change is required.

Review failed jobs from the affected release after the worker restart. Reprocess eligible files whose initial ingestion failed, check interrupted replacements retained their active index, and inspect failed cleanup/refill jobs before retrying them. Do not indiscriminately replay successful paid work.

## Performance choices

A dedicated documents queue is deferred. The current process-per-job worker cannot retain newly loaded Docling models across jobs simply by removing `release_parser_models()`. Keep the existing memory release behavior. Model downsizing needs a separate extraction-quality comparison before changing defaults.

The installed Docling accelerator settings default to four threads and support `DOCLING_NUM_THREADS` / `OMP_NUM_THREADS`. Actual CPU oversubscription must be measured across all workers and other Torch consumers; it is not established merely by the absence of an explicit application setting. Host-specific thread limits belong in the worker service configuration.
