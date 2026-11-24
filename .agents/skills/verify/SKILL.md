---
name: verify
description: The required loop for every code change before it is committed — quality check, end-to-end test, clean up, test again, pre-commit review. Use whenever you finish writing code, before saying a change is done or fixed, and before any commit, push or PR.
---

# Verify a change before commit

Run the five steps in order. A failure at any step means fix it, then start again from step 1.
Commit only after one clean run of all five.

1. **Quality check.** Run the `check` skill: Black, isort and the Django tests for the apps you
   touched (the full suite before the PR). Add or update a test only where it pins real behaviour
   the change introduced. Migrations: generated, reviewed, one leaf per app.
2. **Test as a user.** Restart uvicorn so it runs your code, then run the frontend repo's `e2e`
   skill (`../dare-frontend/.agents/skills/e2e/SKILL.md`) on every row the change touches. A
   change with nothing to see still goes through a real chat or API call, and the stored rows are
   compared with what the provider returned. Passing unit tests prove the code runs, not that the
   feature works.
3. **Clean and enhance.** Reread your own diff against `docs/reference/rules.md`: fix causes, not
   symptoms (no try/except, guards or special cases papering over a bug), delete dead code and
   duplication. Format only the files you touched.
4. **Test again.** Repeat steps 1 and 2 on the same rows. Clean-ups break things.
5. **Before commit.** The diff holds only this task. No Claude co-author trailer. The branch is
   cut from `dev`. The PR description carries the e2e results table and notes deploy order when
   the frontend depends on this change.

End with the results: each step, pass or fail, and the e2e table.

A change that spans both repos runs this loop in each; the frontend has its own `verify`.
