# Ingestion lifecycle validation

Validated locally on 2026-09-14, based on dev `4a5ba89`. No production deployment or production data changes were performed.

## PostgreSQL SSL fork reproduction

A temporary localhost-only PostgreSQL 16 instance used a test SSL certificate. The probe called the actual RQ fork method, with child work limited to database queries.

- Before: the first child reused the parent's PostgreSQL backend. The second child failed with `decryption failed or bad record mac`; the third failed with `SSL SYSCALL error: EOF detected`.
- After: 40 successive child jobs succeeded over SSL, with 40 distinct PostgreSQL backend process IDs, none equal to the startup connection's backend ID.
- Permanent unit coverage verifies cleanup after both successful and failed startup sweeps and before every fork.

## Focused regression tests

131 tests passed against PostgreSQL over SSL, covering worker connection boundaries, cancellation, vector publication/integrity, reconciliation, parsing, OCR continuation, enrichment caching/retries/usage, reprocessing, parser release, and stage reporting.

The lifecycle tests include 17 simultaneous submitted ingestions processed by four threads, with six cancellations. Provider and parser boundaries are mocked in that deterministic test. Cross-connection deletion can commit while embeddings are running; writes and readback run without an open publication transaction. Superseded leases cannot publish or clear the newer lease. Failed replacements retain their map and active generation. Deletion rollback does not enqueue vector cleanup.

Vision tests verify a peak of two concurrent operations, per-operation File/client instances and telemetry, stable result ordering, continuation after one operation fails, cancellation before another batch is submitted, serial platform-funded calls, and client closure on success/failure. Existing structured-output tests verify retry usage is billed and unsuccessful responses are not cached.

## Real sample PDFs and local vector storage

An authenticated API client uploaded 17 PDFs from the supplied `sample_docs/ntsb_reports` directory. The run used an isolated PostgreSQL test database, temporary media directory, and a separate Weaviate collection.

- Actual upload and delete API requests, Basic PDF parsing, chunking, ORM writes, Weaviate upserts, readback verification, publication, and vector deletion.
- Four concurrent document jobs.
- Six files deleted during processing: three during embedding, three after staging identity creation but before the vector upsert. The latter deliberately exercises a write that completes after deletion cleanup has already run.
- Eleven files completed with 124 verified vectors; zero vectors remained outside the surviving files' published generations.
- Paid embedding responses were deterministic mocks. Vision/model quality, live provider latency, Advanced Docling memory usage, production throughput and host sizing were not benchmarked.
- The test's Weaviate collection is removed by cleanup.

## Full-suite baseline and formatting

The complete suite ran 902 tests on a fresh PostgreSQL test database: six failures (two assertion failures and four errors). All six reproduce on unchanged dev:

- Five outcomes in `core.test_rag_evidence_integrity.IndexReplacementTests`: async retrieval closes the TestCase-managed database connection and later tests in the class inherit that failure.
- `conversations.test_gemini_3_8_seed.GeminiSeedTests.test_single_conversations_migration_leaf` expects `0100_seed_gemini_3_8_flash` even though dev's migration leaf is `0101_ensemble_preset`.

A reused baseline database also exposed three seed-dependent fixture errors; these were absent from the fresh full-suite run.

All changed Python files pass Black, isort with the Black profile, compilation, and `git diff --check`. Repository-wide formatting checks still flag existing unrelated formatting drift (Black reports 324 files); it was not reformatted as part of this change.

See [rollout and lifecycle behavior](deployment/ingestion-lifecycle.md) for restart requirements, concurrency configuration and remaining infrastructure trade-offs.
