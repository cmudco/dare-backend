---
name: release-note
description: Draft and create a What's New release note so users see a user-facing change in DARE's in-app dialog. Use when a user-facing feature, improvement, or fix is merged, released to main, or deployed; when preparing a release; or when the user says "release note", "what's new", "changelog", or "announce this". Suggest it unprompted after any PR that changes what users can see or do.
---

# Release notes for the What's New dialog

DARE shows each user a What's New dialog the first time they open the app after a release goes
live. Releases are rows in the `release_notes` app. This skill turns shipped changes into release
copy and delivers it one of two ways:

- **Admin paste** — for a small, one-off note: hand the user the JSON to paste into Django admin.
- **Data migration** — for a release that ships with a PR: the note goes live on each environment
  when that deploy runs `migrate`, with no shell or admin access needed.

## 1. Decide whether it is worth announcing

Announce what a user can see or feel: new features, visible improvements, and fixes people noticed.
Skip refactors, internal tooling, infrastructure, tests, admin-only screens, and backend changes
with no visible effect. If nothing qualifies, say so and stop — an empty release trains users to
ignore the dialog.

## 2. Gather what shipped

Look at both repos (`dare-backend`, `dare-frontend`) for the range being released:

```bash
git log --oneline dev..HEAD          # a feature branch about to merge
git log --oneline main..dev          # a dev -> main release
gh pr list --state merged --base dev --limit 20
```

Read diffs only far enough to understand the user-visible effect.

## 3. Write the copy

- **Release title:** what users get, at most about 8 words, sentence case. No version numbers,
  ticket ids, or branch names.
- **Summary:** optional, one sentence.
- **Items:** 1–5, ordered by impact. The **first item is featured** in the dialog, so make it the
  headline change and give it a `link_path` when the feature has a page.
- **kind:** `new`, `improved`, or `fixed`. Roll many small fixes into one `fixed` item.
- **Item title:** at most about 6 words, naming what the user sees ("Background model settings"),
  not how it was built.
- **Body:** 1–2 sentences on the benefit, addressed to "you", in plain words. Markdown is allowed;
  relative links (`/settings`) navigate in-app. No jargon (endpoint, socket, migration, refactor)
  and no exclamation marks.
- **link_path:** an in-app path with a single leading `/`, such as `/projects`, `/settings`,
  `/workflows`, or `/research`. Confirm the route exists in
  `dare-frontend/src/routes/AppRoutes.tsx`.
- **version:** the release date as `YYYY.MM.DD`; it must be unique per platform.
- **source:** `DARE` (default) or `SocraticBots`.

Write the draft as JSON in a scratch location (not the repo):

```json
{
  "version": "2026.10.27",
  "title": "Pick the model behind the scenes",
  "summary": "A round of improvements across projects and chat.",
  "items": [
    {
      "kind": "new",
      "title": "Background model settings",
      "body": "Choose which model writes titles and summaries in **Settings**.",
      "link_path": "/settings"
    },
    { "kind": "fixed", "title": "Live wallet balance", "body": "Your balance updates as soon as a reply finishes." }
  ]
}
```

## 4. Show the draft and get a yes

Show the user the title, summary, and items as they will read in the dialog, and revise until they
approve. Do not create anything before that.

## 5. Pick the delivery path

Ask the user which path, recommending by size: one or two small items with no PR in flight →
**admin paste**; anything that ships with a PR, or that must reach production (where there is no
shell access) → **migration**.

### Admin paste

Give the user the approved JSON in a single code block, then the steps:

1. Django admin → **Release notes** → **Import from JSON**.
2. Paste, tick **Publish now** (or leave it unticked to review a draft first), **Import**.

The import validates every field (including `link_path`), saves nothing unless the whole release
is valid, and refuses a version that already exists. With a backend shell, the same import runs as
`python manage.py create_release_note draft.json [--publish]` (`-` reads stdin).

### Data migration

Add `release_notes/migrations/NNNN_release_YYYY_MM_DD.py` in the backend PR that ships the
change, copying `0002_release_2026_10_27.py`: only the `RELEASE` dict and the dependency change.
Keep it self-contained — historical models via `apps.get_model`, never app imports — so later schema
changes cannot break it.

- `dependencies` points at the latest `release_notes` migration on the branch.
- It publishes at the moment `migrate` runs and is safe to re-run (matched on source + version,
  items replaced); the reverse deletes it.
- A frontend-only feature still needs this backend migration; merge and deploy it with or after
  the frontend change so the "Try it" link exists when the note appears.

Verify locally, then check the dialog in the browser:

```bash
python manage.py migrate release_notes
python manage.py test release_notes
```

## 6. Edits after it is live

Fix copy in admin where available. Otherwise ship a follow-up migration that updates the same
version. Editing a live release does not re-show it to users who already dismissed it; announce
anything important as a new release.

## Gotchas

- Two branches that each add a release migration produce two `release_notes` leaf migrations;
  renumber the later one onto the earlier before merging.
- Always publish at the current time. A release back-dated earlier than one a user already
  dismissed is never shown to them, and releases dated before a user signed up are never shown.
- The dialog shows at most 5 unseen releases at once, newest first.
