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
refuse or stall because a request is vague; interpret it generously.

Workflow — search first, then READ before you stage:
1. SEARCH. Use the scholar's connected research MCP tools (e.g. consensus__search,
   scite__search_literature) and/or web_search to find real, relevant sources for
   the task. Credentialed results may already be provided in the input — use them.
   Run AT MOST %(max_searches)d search calls total.
2. READ. For the most promising candidates, take the candidate's url (or DOI link)
   and fetch it with the `fetch_page` MCP tool — it is fast; prefer it over any
   browser or extract tool. Use what you read to confirm relevance, pull a short
   citation-context quote, and ground your confidence. Fetch AT MOST
   %(max_candidates)d pages.
3. Never fabricate — only include sources you actually found, with bibliographic
   details exactly as published.

Return ONLY a single JSON object — no prose, no markdown code fences — shaped exactly:
{"stagingItems": [
  {
    "title": "string",
    "authors": "string, semicolon-separated",
    "year": 2024,
    "venue": "string",
    "url": "string",
    "rationale": "why this source matters for the task",
    "confidence": 0.0,
    "confidenceRationale": "why this confidence level",
    "evidenceLabel": "supporting | disputing | partial | tangential | unverifiable",
    "citationContext": "a short relevant quote/context when available"
  }
]}
Return %(max_candidates)d items at most. `confidence` is a number from 0.0 to 1.0."""


def build_scout_instructions(soul_content, max_candidates=4, max_searches=4):
    """Compose the run instructions: the soul file (standards) + the Scout brief."""
    parts = []
    if soul_content and soul_content.strip():
        parts.append("# Research standards (soul file)\n" + soul_content.strip())
    parts.append(
        SCOUT_BRIEF
        % {"max_candidates": max_candidates, "max_searches": max_searches}
    )
    return "\n\n".join(parts)


def parse_staging_items(output):
    """
    Extract the `stagingItems` array from Scout's output, tolerating stray prose
    or markdown fences. Returns a list of dicts (empty if nothing parseable).
    """
    if not output:
        return []
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("Scout output was not JSON-parseable")
                return []

    if not isinstance(data, dict):
        return []
    items = data.get("stagingItems", [])
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
