# Document cancellation and publication

This change keeps the current queues, worker topology, and sequential vision processing. It requires no schema migrations.

## Ownership and cancellation

Parsing, enrichment and status writes are conditioned on the file's owner and ingestion token. Deletion or replacement of the token cancels the old attempt. Cancellation returns without reporting an ingestion error; the worker log records the file ID. A deleted file's attempt rows are deleted with it.

Embedding, entity extraction, vector upsert and readback verification run outside the publication transaction. Prepared map rows stay in memory until verification succeeds. A short transaction then locks the owned file, replaces its map and publishes the verified generation together. A superseded/deleted attempt cannot publish. Unpublished staged vectors are cleaned up on failure/cancellation; a failed replacement retains the previous map and generation.

Deletion locks the current file before snapshotting its vector generations and backends, including staged attempts. Cleanup is queued after commit using those stable identifiers; jobs queued before this change remain supported. Cleanup failures propagate to RQ with three immediate retries, without requiring RQ's separate retry scheduler. Enqueue still depends on Redis availability; durable delivery during a Redis outage would require a transactional outbox.

Reconciliation checks that the observed job ID, token and timestamp still match before marking an attempt interrupted. It cannot overwrite an attempt that changed while the sweep was inspecting RQ.

## Rollout

Deploy the backend and restart the application workers. No migration or new infrastructure is required. Keep reconciliation and the scheduler enabled. The PostgreSQL connection-isolation fix is reviewed separately and should ship first.

Review interrupted files and failed cleanup jobs before selectively retrying them. Do not indiscriminately replay successful paid work. Model selection, OCR approval/page limits, figure limits, parser model release, and vision concurrency remain unchanged.
