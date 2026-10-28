# Contributing

Thanks for contributing to the DARE backend, the Django service behind the DARE platform and
the Socratic Bots apps built on it.

## Filing issues

Search existing issues first and confirm the problem still happens on the latest `dev`.

- **Bugs.** Include your environment (OS, Python version, Docker or bare metal, browser if the
  frontend is involved), minimal numbered steps to reproduce, expected and actual behaviour with
  stack traces or error responses (redact secrets), and how badly it blocks use.
- **Features.** Describe the user-facing problem rather than the implementation you have in mind,
  who it affects, and any constraints or non-goals. For a large architectural change, file an
  issue with a design sketch before writing code.
- **Security issues.** Do not open a public issue. Follow [SECURITY.md](SECURITY.md).

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

- **What.** A one-paragraph summary.
- **Why.** The motivation, with the issue linked (`Closes #123`).
- **How.** The approach, with non-obvious decisions called out.
- **Testing.** How you verified it: new tests, manual steps.
- **Screenshots or recordings.** For any user-facing or API-shape change.
- **Migration notes.** For anything backwards-incompatible.

### Review and merge

- A PR needs at least one approving review from a maintainer and a passing local check suite.
- PRs land as merge commits, so every commit message on the branch must stand on its own.
- Maintainers may rebase on `dev` before merging.
- Review for correctness, then design, then style. Mark comments `nit:`, `consider:` or
  `blocking:`.
- Approve when you would ship the change as it is.

### Commit messages

Use `<type>(<scope>): <summary>`, where type is `feat`, `fix`, `refactor`, `docs`, `test` or
`chore` and the scope is optional. Keep the summary under 70 characters and put context in the
body.

```
feat(rag): index spreadsheets by a summary card, not their rows
fix(chat): keep a running turn attached when the user switches chats
docs(agents): cut AGENTS.md to commands and real traps
```

## Using AI coding agents

`AGENTS.md` holds the commands and traps, and `.claude/CLAUDE.md` imports it for Claude Code.
Procedures (`run-backend`, `run-workers`, `check`, `release-note`) live in `.agents/skills/`, with
symlinks in `.claude/skills/`; [.agents/README.md](../.agents/README.md) explains the layout.
Treat these files like code: if you change how the backend runs, builds or tests, update the
matching skill in the same PR.

## Getting help

Open a draft PR and ask in the description, or file an issue. For anything else, email
vks@andrew.cmu.edu.
