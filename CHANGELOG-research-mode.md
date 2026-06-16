# Research Mode — Changelog

Research Mode adds a delegated research agent to DARE: the scholar asks **Scout**
to find sources, a **Critic** to weigh them, a **Chat** that reasons over approved
knowledge, and a **Presentation Assistant** that produces artifacts — all driven
through the Hermes agent runtime, with DARE as the sole writer of durable truth.

Architecture invariant throughout: **MCP to read · REST to drive · DARE to write ·
the scholar gates what becomes knowledge.**

---

## v1.0 — June 2026

### Delegated agents & flow
- **Scout reads before it stages** — search → fetch the source → stage only
  grounded findings with a verbatim citation context and a confidence rationale.
- **Per-run Hermes sessions** for delegated runs (no history replay → controlled
  token use), with a **project-scoped session key** for long-term recall.
- **Approved knowledge is injected** into Scout and the Presentation Assistant, so
  new work builds on what the scholar already accepted.
- **Informal requests interpreted generously**; a pure greeting / no-intent message
  stages nothing and says so honestly.
- **Chat is grounded** in the project (research question + approved knowledge) and
  points artifact requests to the Artifacts tab.

### Page reading & tools
- **`fetch_page`** — DARE's own page reader, exposed as a gateway builtin.
- Reader rebuilt on **Anthropic's native `web_fetch`** (the same reader DARE's chat
  uses): fast (~2–8s), reads PDFs, and **fails honestly** — a paywall/blocked page
  is reported as a tool error, never a refusal passed off as content.
- **Per-run tool scoping** named in the brief; per-run tool selection honored.
- **Scholarly results compacted** to a few lines per paper (title, authors, DOI,
  citation tally) **plus the abstract**, so the agent can triage — and ground a
  finding from the abstract when the full text is paywalled.

### Evidence & the graph
- **Fetched-document corpus** (`GatewayFetch`) — the complete response of every
  gateway call is captured (page fetches deduped by URL, DOIs extracted).
- **Evidence graph endpoint** — nodes/edges derived deterministically from staged
  sources, run provenance, and the corpus (including paper-to-paper *citation
  mention* edges). No invented scoring.
- Sources declare their `sourceTool` (Scite / Consensus / web) in provenance.

### Artifacts
- Eight renderable types, including **docx and pptx** validated against DARE's
  canonical schemas (one schema, no parallel implementation).
- The Presentation Assistant runs in its own session/mode; precise repair re-asks
  carry the exact validator error; declines cleanly when there's no subject.

### Reliability & audit
- **Tool failures audited honestly** — an errored tool is never treated as a result
  or as evidence.
- **Robust JSON handling** — trailing-junk-tolerant parsing, salvage from malformed
  envelopes, a one-shot repair re-ask, and DB-boundary guards (titleless items are
  dropped, not staged as empty cards).
- **Failed agent runs detected** and surfaced as failures (not silent empty runs);
  `_start_run` retries a transient runtime blip before giving up.
- **Runs audit** stores raw untrimmed tool responses, and each tool call shows its
  **result, fetched URL, token size, and error** — so a run's token cost is
  traceable to individual calls.

### Cost controls
- **Hard per-run budgets** — tool-call count + wall-clock; on breach the run stops
  and **salvages** whatever partial output it has.
- Deep vs. quick Scout depth knobs (searches / candidates).

### Docs (this PR)
- `docs/research/hermes-cli-and-rest-reference.md` — Hermes CLI + REST surface and
  the DARE⇄Hermes contract.
- `docs/deployment/research-mode-hermes.md` — set up & deploy Hermes on a server.
- `docs/research/scite-data-reference.md` — what Scite returns and how Scout uses it.
- `docs/research/research-mode-future-enhancements.md` — the roadmap (memory first).

### Frontend (companion PR)
- Research workspace: Ask Scout, Chat, Review Inbox, Context, **Evidence Graph**,
  Artifacts, and **Runs** (with per-tool-call drill-down: tokens, URL, result,
  error).

---

> Conventions: dates are when the work landed on the feature branch. The roadmap
> for what's next lives in `docs/research/research-mode-future-enhancements.md`.
