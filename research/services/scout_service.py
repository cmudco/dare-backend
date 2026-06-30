"""
Scout delegation helpers — how DARE asks Hermes to find sources and how it reads
the result back.

The contract: DARE sends the task as the run `input` and the soul file + a Scout
brief as `instructions`. Scout uses Hermes's `web_search` tool and returns a single
JSON object of source candidates, which DARE parses into staging items.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

SCOUT_BRIEF = """You are Scout, a research source-finder working under the scholar's standards above.

The scholar's request may be informal, terse, or underspecified — that is fine
and expected. First restate it to yourself as a precise research question that
preserves their intent (use the project's research question and approved
knowledge for context), then execute the workflow for that question. Never
refuse or stall because a request is vague; interpret it generously. The one
exception: if the request carries no research intent at all (a greeting, small
talk, a test message), do not search — return {"stagingItems": []} immediately.

Workflow — search first, then READ before you stage:
1. SEARCH. The scholar's connected research tools (Scite, Consensus, Scholar)
   are run for you inside DARE — when present, their results are already in your
   input. Use those first and PRIORITIZE peer-reviewed scholarly results over
   generic web snippets, drawing candidates from EVERY tool that returned
   results. For coverage they lack you may run web_search (AT MOST
   %(max_searches)d calls total). Do NOT try to call consensus__search,
   scite__search_literature, or any other research tool directly — they run only
   server-side under the scholar's account, never from here.
2. READ before you stage. Fetch a promising source's page with the `fetch_page`
   MCP tool (for papers, the DOI link: https://doi.org/<doi>) and quote the
   fetched text in `citationContext`. If the full text cannot be fetched
   (paywall, fetch error) but the credentialed results gave you the paper's
   abstract, you MAY ground `citationContext` in that published abstract — it
   is the authors' own summary, not a search snippet — and judge the evidence
   from it. Only fall to `evidenceLabel: "unverifiable"` with low confidence
   when you have neither fetched text nor an abstract. Fetch AT MOST
   %(max_candidates)d pages, and read a page ONLY with `fetch_page` (mcp_dare_fetch_page)
   — never `web_extract` or a browser tool, which bypass DARE's audited gateway.
3. Never fabricate — only include sources you actually found, with bibliographic
   details exactly as published, including `doi` whenever known.

Return ONLY a single JSON object — no prose, no markdown code fences — shaped exactly:
{"stagingItems": [
  {
    "title": "string",
    "authors": "string, semicolon-separated",
    "year": 2024,
    "venue": "string",
    "doi": "string or empty",
    "url": "string",
    "rationale": "why this source matters for the task",
    "confidence": 0.0,
    "confidenceRationale": "why this confidence level",
    "evidenceLabel": "supporting | disputing | partial | tangential | unverifiable",
    "citationContext": "a short relevant quote/context when available",
    "sourceTool": "which search surfaced this candidate: scite | consensus | web"
  }
]}
Stage as many genuinely relevant sources as the evidence justifies, up to
%(max_candidates)d — do not pad to reach the cap, and do not drop a strong
source just to stay short. `confidence` is a number from 0.0 to 1.0."""


def build_scout_instructions(soul_content, max_candidates=4, max_searches=4):
    """Compose the run instructions: the soul file (standards) + the Scout brief."""
    parts = []
    if soul_content and soul_content.strip():
        parts.append("# Research standards (soul file)\n" + soul_content.strip())
    parts.append(
        SCOUT_BRIEF % {"max_candidates": max_candidates, "max_searches": max_searches}
    )
    # Live tool surface (Path B): the gateway exposes only DARE-owned,
    # credential-free builtins. The scholar's research tools run server-side
    # (their results are injected into the input), so they aren't callable here.
    parts.append(
        "TOOLS FOR THIS RUN: web_search and mcp_dare_fetch_page only. The "
        "scholar's research tools are not callable from here — their results "
        "are already in your input."
    )
    return "\n\n".join(parts)


def find_json_object(text, required_key=None):
    """
    Scan for the first complete JSON object in `text` (optionally one that has
    `required_key`), tolerating prose around it AND trailing garbage after it —
    models sometimes echo the contract template after the real object, which
    breaks whole-string parsing and any greedy first-{-to-last-} match.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    fallback = None
    while index != -1:
        try:
            obj, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(obj, dict):
            if required_key is None or required_key in obj:
                return obj
            if fallback is None:
                fallback = obj
        index = text.find("{", index + 1)
    return fallback


def _scan_objects(text):
    """Yield every complete JSON object found anywhere in `text`."""
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            obj, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        yield obj
        index = text.find("{", end)


def parse_staging_items(output):
    """
    Extract the `stagingItems` array from Scout's output, tolerating stray prose,
    markdown fences, and trailing junk. Returns a list of dicts (empty if
    nothing parseable).
    """
    if not output:
        return []
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    data = find_json_object(text, required_key="stagingItems")
    if isinstance(data, dict) and "stagingItems" in data:
        items = data.get("stagingItems", [])
        return (
            [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
        )

    # Salvage: a malformed envelope (e.g. the model never closed the outer
    # brace) still contains complete, individually-parseable item objects.
    if '"stagingItems"' in text:
        items = [
            o
            for o in _scan_objects(text)
            if isinstance(o, dict) and o.get("title") and "stagingItems" not in o
        ]
        if items:
            logger.warning(
                "Scout envelope was malformed; salvaged %d item(s)", len(items)
            )
            return items

    logger.warning("Scout output was not JSON-parseable")
    return []
