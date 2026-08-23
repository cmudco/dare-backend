---
name: run-workers
description: Start and check the DARE RQ background workers that process file uploads (parse, chunk, embed, upsert to the vector store). Use when the user uploads or vectorizes files, works on RAG ingestion, or asks why an uploaded file is stuck and never reaches "completed".
---

# Run the DARE background workers

The backend offloads file processing — parse → chunk → embed → upsert to the vector store — to
**RQ workers** reading the `default` Redis queue. Without a worker running, uploads are accepted
and then sit unprocessed forever.

## When you need this

- Uploading, embedding, or vectorizing files (RAG ingestion).
- Any long-running offloaded job.
- **Not** needed for plain chat — start it only when the task involves files.

If you are on the Docker path (`docker compose up`), a worker container is already running and
this skill does not apply.

## Prerequisites

- Redis on `:6379` (`redis-server`).
- The backend venv, activated: `source .venv/bin/activate`.

## Start a worker

```bash
python manage.py rqworker default -v 2
```

### On macOS

RQ's default forking worker crashes on macOS under the Objective-C runtime. Use `SimpleWorker`,
which runs jobs in-process instead of forking:

```bash
python manage.py rqworker default --worker-class rq.SimpleWorker -v 2
```

The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` prefix is a partial workaround for the same problem
and you will see it in older notes, but `--worker-class rq.SimpleWorker` is the reliable fix.

Do **not** pass `--max-jobs`. The worker exits once it hits that count, which looks identical to a
crash and leaves later uploads unprocessed.

## Verify

```bash
python manage.py rqstats          # queue depth and worker count
```

A worker is doing its job when an uploaded file moves off `processing` within a few seconds. If
files upload but never complete, check the worker is alive before looking anywhere else — this is
the single most common cause.

## Run it in the background

```bash
nohup python manage.py rqworker default --worker-class rq.SimpleWorker -v 2 \
  > /tmp/dare-worker.log 2>&1 & disown
tail -f /tmp/dare-worker.log
```

## The memory queue — exactly ONE worker, ever

The post-reply memory writer runs on its own `memory` queue (needs `USE_POSTGRES=True`):

```bash
nohup python manage.py rqworker memory --worker-class rq.SimpleWorker -v 2 \
  > /tmp/dare-memory-worker.log 2>&1 & disown
```

**Never start a second `memory` worker.** One user's turns must be ingested in order — turn N's
collision checks read turn N-1's rows — and a single worker is global FIFO, which is that
guarantee with no locking. A second worker silently breaks ordering; the damage is bounded to
spurious supersedes, but bounded is not correct.

## Related

- `run-backend` — start the API first; the worker shares its venv and `.env`.
