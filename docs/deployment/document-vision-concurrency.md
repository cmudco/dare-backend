# Document vision concurrency

Page transcription and figure description can run in bounded parallel batches. Each operation owns its File instance, provider client, telemetry, and thread database connections. Results retain input order; a cancelled ingestion stops submitting further batches. Async provider clients close within the event loop that used them.

`DOCUMENT_ENRICHMENT_CONCURRENCY` defaults to 2 and is bounded between 1 and 4. Set it to 1 to return to serial execution. This limit applies per document job, so account for the total worker count when sizing provider traffic.

All wallet types use the same concurrency setting. DARE requests retain the estimated-credit check before each paid request. Completed document-vision calls are recorded atomically even if concurrent charges take the balance negative; this does not reserve funds or impose a strict overspend limit across jobs. Other billing callers retain the default insufficient-balance rejection. Cache behavior and provider usage accounting remain part of each operation.

Deploy after the ingestion lifecycle change and restart backend/workers. No database migration, queue topology, parser model, OCR limits, or figure limits change.

Tests cover bounded concurrency, isolated operation state, ordered results, cancellation, partial failures, all wallet types, serial configuration, concurrent wallet settlement, and async client cleanup. Paid-provider latency and extraction-quality benchmarks have not been run; this change makes no measured production speedup claim.
