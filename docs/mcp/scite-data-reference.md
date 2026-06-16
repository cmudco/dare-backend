# Scite — what data it gives us, and how Research Mode uses it

This documents the **real** data DARE receives from the Scite MCP server, captured
from live calls, so we know exactly what we're working with (and what we'd lose
without it). Scite is the scholarly workhorse behind Scout.

## What Scite is

A scholarly search engine **plus** a citation-intelligence database. Its unique
value over a plain search engine: for every paper it tracks *how it has been
cited* — whether citing papers **support**, **contrast**, or merely **mention**
it ("smart citations"). That citation direction is computed by Scite; nobody
else hands it to us.

The scholar connects their own Scite account to DARE over MCP. DARE calls Scite
on their behalf (credentials stay in DARE), so no keys leave the platform.

### Subscription / usage (as of the trial)

| Plan | Price | MCP tool uses / month |
|------|-------|------------------------|
| Basic (was "Premium") | $12/mo (billed annually) | **250** |
| Pro | $40/mo (billed annually) | 2,500 (+ patents, 10k-paper collections, more datasets) |

A scout run makes ~1–3 Scite calls (one pre-fetch + the agent's own searches),
so **Basic's 250/mo ≈ 80–250 scout runs/month**. In production the **client uses
their own Scite key**; DARE just routes the calls.

## The tool we use: `search_literature`

This is the one Scout relies on. One call returns up to 10 papers.

### Top-level response
```json
{ "total": 1775448, "count": 10, "query": "...", "limit": 10, "offset": 0, "hits": [ ... ] }
```

### Per-paper fields (one `hits[]` entry) — REAL example
```json
{
  "doi": "10.1037/cep0000336",
  "title": "Towards mechanistic investigations of numerical and music cognition.",
  "authors": [{ "authorName": "Dominique T. Vuvan" }, { "authorName": "Jessica Sullivan" }],
  "journal": "canadian journal of experimental psychology",
  "publisher": "American Psychological Association (APA)",
  "abstract": "Are there cognitive connections between humans' ability to make music and their understanding of math and numbers? ...",
  "year": 2025,
  "date": "2025-06-01",
  "volume": "79", "issue": "2", "page": "189-194",
  "tally": { "total": 2, "supporting": 0, "contrasting": 0, "mentioning": 2, "citingPublications": 2 },
  "citations": [ { "sourceDoi": "...", "targetDoi": "...", "section": "..." } ],
  "isOa": false,
  "oaStatus": "closed",
  "relevancyScore": 244.17,
  "contentDenied": true,
  "access": { "url": "https://www.reprintsdesk.com/landing/..." }
}
```

### Field-by-field — what it is and how DARE uses it

| Field | What it is | How Research Mode uses it |
|-------|-----------|---------------------------|
| `doi` | Permanent paper ID | Unique key; staged as `doi`; builds `https://doi.org/<doi>` for fetch + the evidence graph |
| `title`, `authors`, `journal`, `publisher`, `year`, `date`, `volume`/`issue`/`page` | Bibliographic record | Staged verbatim onto the finding (no fabrication) |
| `abstract` | The authors' own summary (~1k chars) | **Carried into the agent's context** (compacted view) for triage and — when the full text is paywalled — to ground `citationContext` |
| `tally.supporting` / `.contrasting` / `.mentioning` | **Smart-citation counts** — how later papers cited this one | The strongest signal for the `evidenceLabel` (supporting / disputing / partial). This is Scite's unique contribution |
| `citations[]` | Sample of citing papers (`sourceDoi`→`targetDoi`, section) | Seeds future paper-to-paper edges in the evidence graph |
| `isOa` / `oaStatus` | Open-access status | Tells the agent whether a `fetch_page` is worth attempting (closed → likely paywall) |
| `contentDenied` | Full text is gated | Flags that fetching will fail — lean on the abstract instead |
| `relevancyScore` | Scite's ranking | Ordering only; DARE does **not** invent its own score |
| `access.url` | A (often paywalled) access link | Not used for staging; the DOI link is preferred |

### Why the abstract + tally matter most
Together they're often **enough to stage a high-quality finding without opening
the paper**: the abstract gives the claim and a quotable line, the tally gives
the evidence direction. The full-text `fetch_page` is reserved for promising
papers that need a deeper read — and academic papers are frequently PDFs or
paywalled (`contentDenied: true`), so the abstract is often the most reliable
text we get.

### What DARE injects vs. stores
- **Stored (audit):** the complete raw response, untrimmed — visible in the Runs view.
- **Injected (agent context):** a compacted view — title, authors, year, journal,
  DOI link, the citation tally, **and a ~700-char abstract** per paper. ~5× smaller
  than raw, nothing important dropped.

## The other 13 tools (specialized datasets)

Scout doesn't use these by default; they're domain-specific and only relevant to
medical/regulatory research. Catalogued for completeness:

| Tool | Dataset |
|------|---------|
| `search_patents` | Patents |
| `search_clinical_trials` / `get_clinical_trial` | Clinical trials |
| `search_grants` / `get_grant` | Research grants (NIH RePORTER, NSF, ...) |
| `search_device510k` / `get_device510k` | FDA 510(k) device clearances |
| `search_510k_summaries` / `get_510k_summary` | FDA 510(k) summary PDFs |
| `search_mhra` / `get_mhra_alert` | MHRA medicine/device safety alerts |
| `search_maude` / `get_maude_report` | FDA MAUDE adverse-event reports |

`search_*` tools share params `q` (query), `f` (filters), `p` (page), `s`/`sortDir`
(sort); `get_*` tools take an `id` to fetch one record's full detail.

## Bottom line for the subscription decision

Scite is **load-bearing** for Research Mode: it provides the scholarly search,
the abstracts that ground findings, and — uniquely — the supporting/contrasting
citation tally that drives the evidence labels and the graph's colour. Consensus
(also connected) overlaps on search but **does not** give the citation direction,
and web search gives neither. Drop Scite and the feature still runs, but it loses
its sharpest evidence signal.

For the pilot, keep Scite (Basic, $12/mo is cheap and the trial covers now). In
production the client supplies their own Scite key, sized to their volume
(Basic 250/mo for light use, Pro 2,500/mo for heavy).
