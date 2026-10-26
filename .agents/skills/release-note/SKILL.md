---
name: release-note
description: Draft and create a What's New release note so users see a user-facing change in DARE's in-app dialog. Use when a user-facing feature, improvement, or fix is merged, released to main, or deployed; when preparing a release; or when the user says "release note", "what's new", "changelog", or "announce this". Suggest it unprompted after any PR that changes what users can see or do.
---

# Release notes for the What's New dialog

DARE shows each user a What's New dialog the first time they open the app after a release goes
live. Releases live in the database (`release_notes` app) and are published from Django admin. This
skill turns shipped changes into a release note, creates it as a **draft**, and leaves publishing
to a person.

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

## 5. Create the draft

From an activated venv at the backend root:

```bash
python manage.py create_release_note /path/to/draft.json
```

The command validates every field (including `link_path`), rolls back on any error, refuses a
duplicate version, and never publishes. It prints the admin URL of the new draft.

For a deployed environment, run the same command in that environment's backend, piping the JSON
on stdin with `-` (for example over ssh). Writing to a shared environment needs the user's explicit
go-ahead first. Where no shell access exists, enter the same content in Django admin under
**Release notes → Add**.

## 6. Publish

Publishing is the user's call. In admin, open the draft and set **Published at** to now, or use the
**Publish selected releases now** action. A future time schedules it. Users see it on their next
app load.

## Gotchas

- Always publish at the current time. A release back-dated earlier than one a user already
  dismissed is never shown to them, and releases dated before a user signed up are never shown.
- The dialog shows at most 5 unseen releases at once, newest first.
- Editing a live release does not re-show it to users who already dismissed it; ship a new release
  for anything important.
