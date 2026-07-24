---
name: check
description: Run the DARE backend's local check suite — Black, isort, and the Django test suite — before opening a pull request. Use when the user is about to commit, push, or open a PR, or asks whether their change passes CI.
---

# Check the DARE backend before a PR

Run these from an activated venv at the repo root. All three must pass before a PR is opened —
they are the same checks CI runs.

```bash
black --check .
isort -c .
python manage.py test
```

If formatting fails, apply it rather than hand-editing:

```bash
black .
isort .
```

## If the change touches models

Generate and commit the migration in the same PR as the model change:

```bash
python manage.py makemigrations
```

Review the generated file before committing. An unreviewed `makemigrations` can pick up drift from
someone else's unmerged model change, or emit a destructive operation you did not intend.

Check that the migration graph still has a single leaf per app:

```bash
python manage.py showmigrations
```

## If the change touches the API

Regenerate the OpenAPI schema so `docs/api/dare-backend.md` and the served schema stay in sync with
the code.

## Branch and PR conventions

Feature branches are cut from `dev`, not `main`:

```bash
git checkout dev
git pull --ff-only origin dev
git checkout -b your-name/feature/short-description
```

Naming is `<author>/<feature|fix|refactor|docs|chore>/<short-description>`.

The PR description should cover **what**, **why**, **how**, and **how it was tested**, plus
screenshots for any user-facing or API-shape change and migration notes if the change is
backwards-incompatible.

## Related

- `CONTRIBUTING.md` — the full contribution guide this skill summarizes.
- `rules.md` — the repo's coding standards, in detail.
