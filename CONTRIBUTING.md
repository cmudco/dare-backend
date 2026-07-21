# Contributing to DARE Backend

Thanks for your interest in contributing. This guide covers issues, pull requests, and the coding standards we follow.

The canonical online version of this guide is available at [https://github.com/cmudco/dare-backend/blob/main/CONTRIBUTING.md](https://github.com/cmudco/dare-backend/blob/main/CONTRIBUTING.md).

## Filing issues

Before opening an issue:

1. Search the repository's existing issues to avoid duplicates.
2. Verify the bug reproduces against the latest `dev`.
3. Gather: environment (OS, Python version), reproduction steps, expected vs. actual behaviour, relevant logs.

### Bug reports

Include:

- **Environment** — OS, Python version, deployment path (Docker / bare-metal), browser if frontend-related.
- **Steps to reproduce** — minimal, numbered.
- **Expected behaviour**.
- **Actual behaviour** — including stack traces or error responses (redact secrets).
- **Severity** — does this block use, or is it a minor annoyance?

### Feature requests

Include:

- The user-facing problem you're trying to solve (not the implementation you have in mind).
- Who is affected.
- Any constraints or non-goals.

### Security issues

**Do not** open public issues for security vulnerabilities. See [SECURITY.md](https://github.com/cmudco/dare-backend/blob/main/SECURITY.md) for the disclosure process.

---

## Pull requests

### Before opening a PR

1. Fork the repository, sync the latest `dev`, and create a feature branch from `dev`:

   ```bash
   git checkout dev
   git pull --ff-only origin dev
   git checkout -b your-name/feature/short-description
   ```

   Naming: `<author>/<feature|fix|refactor|docs>/<short-description>`.

2. Run the full local check suite:

   ```bash
   black --check .
   isort -c .
   python manage.py test
   ```

3. If your change touches the database, generate migrations and commit them:

   ```bash
   python manage.py makemigrations
   ```

4. If your change touches the API, regenerate the OpenAPI schema if applicable.

### PR template

Each PR should include:

- **What** — one-paragraph summary.
- **Why** — motivation; link to the issue if there is one (`Closes #123`).
- **How** — high-level approach. Call out non-obvious decisions.
- **Testing** — how you verified the change. New tests added? Manual repro steps?
- **Screenshots / recordings** — for any user-facing or API-shape change.
- **Migration notes** — if backwards-incompatible.

### Review and merge

- At least one approving review from a maintainer.
- All CI checks green (lint, tests).
- Squash-merge by default. The squashed commit message becomes part of release notes — make it descriptive.
- Maintainers may rebase on `dev` before merging.

---

## Coding standards

### Python style

- **Formatter:** [Black](https://black.readthedocs.io) (line length 88). Run `black .` before committing.
- **Import order:** [isort](https://pycqa.github.io/isort/). Run `isort .`.
- **Type hints** are encouraged on new code, especially in `core/services/` and any module exposing a public surface.
- **Docstrings** on services and complex methods. One-line for trivial functions; numpydoc / Google style for anything heavier.

### Django conventions

- Models inherit from `BaseModel` (provides `created_at`, `updated_at`, `is_active`, `is_deleted`).
- Use `Model.active_objects` for default-filtered querysets; `Model.objects` for unfiltered.
- Constants belong in per-app `constants.py` files using `TextChoices` or `IntegerChoices`.
- Wrap user-facing strings with `gettext_lazy as _`. **Do not** translate logged exceptions or developer-facing error messages.
- Keep views thin; push business logic into `core/services/` or model methods.

### REST API

- Inherit from `viewsets.ModelViewSet` for standard CRUD.
- Always declare `permission_classes`. Default to `[IsAuthenticated]`.
- Filter querysets by user in `get_queryset()` for any user-owned resource.
- Use serializer field separation for read vs. write (see [docs/code-standards.md](https://github.com/cmudco/dare-backend/blob/main/docs/code-standards.md) and [docs/serialization.md](https://github.com/cmudco/dare-backend/blob/main/docs/serialization.md)).

### Background jobs

- Anything blocking, slow, or external goes through Django RQ.
- Decorate with `@job('default')`.
- Idempotent where possible — workers may retry on failure.
- Log liberally; failures are inspected via `rqstats` and the worker log.

### Tests

- Use Django's `TestCase` for DB-dependent tests, `SimpleTestCase` otherwise.
- Mock external services (LLM APIs, vector stores, email) — never hit them in tests.
- Cover permission boundaries: a user should not be able to read another user's data.
- Async consumer tests use `ChannelsLiveServerTestCase` or `pytest-asyncio`.

### Commit messages

Conventional-ish: `<type>: <summary>` where type is one of `feat`, `fix`, `refactor`, `docs`, `test`, `chore`. Keep the summary under 70 chars; use the body for context and rationale.

Example:

```
feat: add streaming TTS support to /chat namespace

Wires ElevenLabs into AIService.stream_chat_completion via an
optional `tts=True` flag. Audio chunks are emitted as base64
on a new `audio_chunk` Socket.IO event.

Closes #482.
```

### Code review etiquette

- Review for correctness, then design, then style. Style nits are last priority.
- Suggest, don't demand — `nit:`, `consider:`, and `blocking:` prefixes help calibrate.
- Approve when you'd be comfortable shipping the change as-is.

---

## Getting help

- Stuck? Open a draft PR and ask in the description — easier than hashing things out in chat.
- For larger architectural changes, file an issue with a design sketch _before_ writing code.
