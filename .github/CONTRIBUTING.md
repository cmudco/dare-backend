# Contributing

Thanks for contributing to the DARE backend, the Django service behind the DARE platform and the
Socratic Bots apps built on it.

## Filing issues

1. **Search first.** Look through
   [open and closed issues](https://github.com/cmudco/dare-backend/issues?q=is%3Aissue), and check
   that the problem still happens on the latest `dev`.
2. **Pick the right repo.** API errors, worker failures and ingestion problems belong here. If the
   API answers correctly but the page looks wrong, file it on
   [dare-frontend](https://github.com/cmudco/dare-frontend/issues/new/choose).
3. **Open a form.** Go to
   [Issues → New issue](https://github.com/cmudco/dare-backend/issues/new/choose) and choose **Bug
   report**, **Feature request** or **Docs problem**. Each form asks for exactly what we need; fill
   in every required field.
4. **Redact.** Remove keys, tokens and student data from logs and screenshots.
5. **Watch the issue.** If we need more detail, we ask there.

Before a large change, such as a new app, a schema change across apps, or a new service, open a
feature request with a short design sketch and wait for a reply before you write code.

Security vulnerabilities never go in an issue. Follow [SECURITY.md](SECURITY.md).

## Pull requests

### Branching

Fork the repository and branch from `dev`, not `main`:

```bash
git checkout dev
git pull --ff-only origin dev
git checkout -b your-name/feat/short-description
```

Name branches `<author>/<feat|fix|refactor|docs|chore>/<short-description>`.

### Before you open a PR

Run the check suite (or the `check` skill). CI only deploys, so nothing else catches a failure.

```bash
black --check .
isort -c .
python manage.py test
```

- **Models changed?** Run `python manage.py makemigrations`, review the file and commit it in the
  same PR.
- **API changed?** Regenerate the OpenAPI schema.
- **Code style.** Follow [docs/reference/rules.md](../docs/reference/rules.md).

### PR description

The [pull request template](pull_request_template.md) asks for what, why, how, testing and AI
assistance, plus a checklist. Fill in every section; write "none" where nothing applies. Add
migration notes for anything backwards-incompatible, and screenshots for an API-shape change.

### Review and merge

- A PR needs at least one approving review from a maintainer and a passing local check suite.
- PRs land as merge commits, so every commit message on the branch must stand on its own.
- Maintainers may rebase on `dev` before merging.
- Review for correctness, then design, then style. Mark comments `nit:`, `consider:` or `blocking:`.
- Approve when you would ship the change as it is.

### Commit messages

Use `<type>(<scope>): <summary>`, where type is `feat`, `fix`, `refactor`, `docs`, `test` or `chore`
and the scope is optional. Keep the summary under 70 characters and put context in the body.

```
feat(rag): index spreadsheets by a summary card, not their rows
fix(chat): keep a running turn attached when the user switches chats
docs(agents): cut AGENTS.md to commands and real traps
```

## Using AI coding agents

`AGENTS.md` holds the commands and traps, and `.claude/CLAUDE.md` imports it for Claude Code.
Procedures (`run-backend`, `run-workers`, `check`, `release-note`) live in `.agents/skills/`, with
symlinks in `.claude/skills/`; [.agents/README.md](../.agents/README.md) explains the layout. Treat
these files like code: if you change how the backend runs, builds or tests, update the matching
skill in the same PR.

## Getting help

Open a draft PR and ask in the description, or file an issue. For anything else, email
vks@andrew.cmu.edu.
