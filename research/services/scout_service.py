"""
Scout delegation helpers — how DARE asks Hermes to find sources and how it reads
the result back.

The contract: DARE sends the task as the run `input` and the soul file + a Scout
brief as `instructions`. Scout searches and reads with DARE's gateway tools and
stages each source with the `stage_finding` tool as it goes. An older Scout reply
that still carries a JSON envelope of candidates is parsed as a fallback.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

SCOUT_BRIEF = """You are Scout, a source-finder working under the user's standards above.

The request may be informal or terse; that is expected. Restate it to yourself
as a precise question that keeps their intent (use the project memory and the
conversation you were launched from), then work it. If it has no research
intent at all (a greeting, a test), stage nothing and say so in one line.

WHAT COUNTS AS A GOOD SOURCE HERE: %(kind_guidance)s Your memory says what the
user trusts; let it decide, and label every source honestly with its `kind`.

Pass run="%(run_token)s" on EVERY DARE tool call below.

1. SEARCH: mcp__dare__web_search for the web, mcp__dare__search_papers for
   published research. At most %(max_searches)d searches in total.
2. READ before you stage: open the page with mcp__dare__fetch_page and take the
   facts from what you read. Paywalled DOIs are read from a free copy when one
   exists. Read at most %(max_candidates)d pages.
3. STAGE each source the moment you have read it, with mcp__dare__stage_finding.
   One call per source, at most %(max_candidates)d. Give its working URL, and in
   citationContext copy one short sentence exactly as it appears on the page:
   DARE checks the link, the quote, the DOI and the date before the user sees
   it, and tells you if it refused the source or the user already has it.
4. Before you finish, call mcp__dare__report_learning once: `applied` = each
   remembered preference or skill that changed what you did, with its effect;
   `setAside` = each source you held back because of one, with the reason.
   Empty lists are fine; never invent one.
5. Reply with two or three plain sentences: what you staged and what is still
   missing. No JSON, no long report.

Never fabricate: stage only sources you actually read, with details exactly as
published. Do NOT use your memory or skill tools in a Scout run, and never use
a runtime-native web, extract or browser tool."""


def build_scout_instructions(
    soul_content, run_token, max_candidates, max_searches, kind_guidance=""
):
    """Compose the run instructions: the soul file (standards) + the Scout brief."""
    parts = []
    if soul_content and soul_content.strip():
        parts.append(soul_content.strip())
    parts.append(
        SCOUT_BRIEF
        % {
            "max_candidates": max_candidates,
            "max_searches": max_searches,
            "run_token": run_token,
            "kind_guidance": kind_guidance
            or "Whatever answers the question best, labelled for what it is.",
        }
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


def _clean_rows(rows, keys, limit=20):
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        cleaned = {k: str(row.get(k) or "").strip()[:500] for k in keys}
        if any(cleaned.values()):
            out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def parse_scout_learning(output):
    """The run's "because of you" report: what approved memory changed.

    Returns ``{"applied": [...], "setAside": [...]}`` or ``{}`` when the
    Scout reported nothing (or its envelope had no learning block).
    """
    data = find_json_object(output or "", required_key="stagingItems")
    learning = data.get("learning") if isinstance(data, dict) else None
    if not isinstance(learning, dict):
        return {}
    applied = _clean_rows(learning.get("applied"), ("memory", "effect"))
    set_aside = _clean_rows(learning.get("setAside"), ("title", "url", "reason"))
    if not applied and not set_aside:
        return {}
    return {"applied": applied, "setAside": set_aside}
