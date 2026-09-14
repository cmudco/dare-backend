# Document vision concurrency

Page transcription and figure description can run in bounded parallel batches. Each operation owns its File instance, provider client, telemetry, and thread database connections. Results retain input order; a cancelled ingestion stops submitting further batches. Async provider clients close within the event loop that used them.

`DOCUMENT_ENRICHMENT_CONCURRENCY` defaults to 2 and is bounded between 1 and 4. Set it to 1 to return to serial execution. This limit applies per document job, so account for the total worker count when sizing provider traffic.

DARE-funded wallets remain serial to preserve the existing credit-check/reservation behavior. Parallelism applies to external-key routes. Cache behavior and provider usage accounting remain part of each operation.

Deploy after the ingestion lifecycle change and restart backend/workers. No database migration, queue topology, parser model, OCR limits, or figure limits change.

Tests cover bounded concurrency, isolated operation state, ordered results, cancellation, partial failures, the serial platform-wallet path, and async client cleanup. Paid-provider latency and extraction-quality benchmarks have not been run; this change makes no measured production speedup claim.
