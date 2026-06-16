# Research Mode — Future Enhancements (Roadmap)

Research Mode is shipped and robust: Scout (search → read → grounded staging),
Critic, grounded Chat, eight artifact types, the review-gated knowledge pipeline,
the evidence graph, hard run budgets, honest failure states, and a full audit
trail. This document is the **prioritized roadmap** of where it goes next.

**Memory is the headline.** It's the single biggest lever on quality and on the
"feels like it knows my project" experience, so it leads.

---

## 1. Memory — the flagship track

### Where we are today
- **DARE is the source of truth.** Durable knowledge lives in Postgres, scoped
  per project; the scholar gates everything that becomes durable.
- **Agent-side memory** is handled with Hermes sessions: each delegated run gets
  a *fresh* session (so it doesn't replay history and inflate tokens), while a
  project-scoped **session key** (`dare-proj{id}`) pins long-term recall to the
  project.
- **Limitation:** there is no true per-scholar/per-project *isolation* on the
  agent side beyond that key, and the agent's own profile memory files
  (`MEMORY.md` / `USER.md`) are global to the single shared profile.

### 1a. Per-scholar Hermes profiles (multi-tenancy) — biggest structural win
Nous shipped a **Profile Builder**: each profile is its own home directory
(`config.yaml`, `.env`, `SOUL.md`) with its **own memory, sessions, skills, and
state database**. One profile per scholar gives us real isolation — separate
memory, separate credentials, even a different brain/keys per scholar (so a
client can run on *their* API key).

- **DARE side:** a small `HermesEndpoint` record per scholar (`base_url`,
  `api_key`); `get_hermes_service(user)` resolves it and falls back to the global
  env when none exists. ~1-day, **behavior-neutral** refactor — everything already
  flows through one `HermesService`.
- **Ops side:** a `systemd` template unit per profile + a port convention; move to
  containers (one profile = one volume) at scale.

### 1b. Honcho memory provider — per-project memory on one gateway
Hermes supports **Honcho** as a pluggable memory provider that isolates memory
per session/project *without* running N separate gateways. Lighter than full
profiles when **memory isolation is the only goal** (no separate credentials/brain
needed).

### 1c. DARE-rendered `MEMORY.md` / `USER.md` (v2)
Since DARE owns durable truth, **render the agent's memory files from DARE's
record** (per project/scholar) at run time, rather than relying on the agent's
auto-memory loop. Deterministic, auditable, and fully under DARE's control — the
memory the agent sees is exactly what DARE decided it should see.

### 1d. Layered memory (R&D)
Move past flat session memory toward a layered model (working / episodic /
semantic), tied to the retrieval track below. This is the longer-horizon research
direction.

---

## 2. Retrieval quality — hybrid RAG (past naive vector search)

> Scope note: this is broader than Research Mode — it's the platform's core RAG —
> but it directly raises the quality of everything the agent reads.

**Today** the platform's retrieval is the textbook "naive RAG": fixed-size
chunking + pure vector (cosine) similarity. That fails on exactly what researchers
type — author names, acronyms, DOIs, years, "GPT-4o" — which embeddings smear.

- **Hybrid search:** run **BM25 / Postgres full-text** alongside vector search and
  fuse the results (reciprocal-rank fusion). Exact-token matches that vectors miss,
  recovered deterministically.
- **Structure-aware chunking:** split on headings/sections, not blind character
  counts, so tables and sections aren't cut mid-thought.
- **Make the fetch corpus searchable:** we already capture the full text of every
  page the agent reads (`GatewayFetch`). A full-text index over it gives
  "search everything my project has ever read."
- **Evaluation set first:** ~20 real queries with known-correct documents, so
  "improved retrieval" is measured, not vibes.

---

## 3. Per-project gateway credential scoping (structural)

**Today** one MCP gateway exposes the scholar's whole connected toolbox; a run
narrows its toolset at the **prompt level** only. **Enhancement:** scope
credentials per project/scholar **at the gateway** (pairs naturally with profiles,
§1a) so a run *physically* sees only its permitted tools — closing the gap where a
model could call a tool that was meant to be off for that run.

---

## 4. Evidence graph v2 — AI-proposed paper-to-paper edges

**Today** every graph edge is deterministic: evidence label (source→question),
the scout-request hub, and **citation-mention** edges derived from the fetch
corpus (one paper's text contains another's DOI).

**Enhancement:** a budgeted delegated run proposes *cross-paper relationships* —
"A contradicts B," "builds on," "same method/benchmark." The proposals land in the
**review inbox** (scholar-gated, same invariant: DARE writes, scholar approves).
Scite's smart-citation data + the fetch corpus seed it, so it stays grounded.

---

## 5. Cost & transparency

- **Prompt-cache tuning (paid models):** Hermes caches the stable instruction
  prefix on Claude, but the *growing* tool-results (fetched papers) aren't cached,
  so they're re-billed each turn. Placing cache breakpoints **after** tool results
  would flip the bulk of a run's input tokens to ~1/10th price — the highest-
  leverage cost win on Sonnet. (Verify against / request from Hermes config.)
- **Scite-first staging (optional):** stage from the abstract + citation tally by
  default and `fetch_page` only when the abstract is insufficient *and* the source
  is open-access — cuts the dominant fetch cost with little quality loss.
- **Frontend visibility:** a per-turn token + cache-read breakdown; a browsable
  "fetched pages / DOIs" library (the data already exists in `GatewayFetch`); and
  showing the **fetch-failure reason** (capture gateway-side errors so the audit
  reads "paywall" instead of just "error").

---

## 6. Structured output (when Hermes ships it)

**Today** we get reliable structured results with a prompt-level JSON contract +
a tolerant parser + a one-shot repair re-ask + DB-boundary guards. It's robust,
but it's a workaround for Hermes's API not enforcing schemas. **When Hermes
exposes real structured / function-calling output with schema enforcement, we
delete the tolerant-parser + repair scaffolding entirely.** Worth tracking their
releases.

---

## 7. Smaller backlog items

- Surface the fetch-failure reason in the audit (capture gateway-side errors).
- Memory-proposal accept endpoint; soul-file editor UI; project delete endpoint.
- A DARE service-key auth class for the gateway (vs. the minted JWT used now).
- "Edit with agent" artifact versioning.
- Retire the `FORCE_RESEARCH_ACCESS` dev flag before broad rollout.

---

## Suggested sequence

1. **Per-scholar profiles (§1a)** — unlocks isolation, per-client keys, and is the
   foundation for gateway credential scoping (§3).
2. **Cost/transparency quick wins (§5)** — cache tuning + the visibility views.
3. **Hybrid retrieval (§2)** — platform-wide quality lift.
4. **Evidence graph v2 (§4)** and **layered/Honcho memory (§1b–1d)** as the
   research-forward track.
