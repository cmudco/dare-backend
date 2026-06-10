"""
Critic delegation helpers — how DARE asks Hermes to pressure-test a staged source.

The Critic's job is adversarial: decide whether a source genuinely supports the
use it was staged for, under the scholar's standards. It may read the source
(web_fetch) and returns a single JSON verdict, which DARE attaches to the staging
item's critic_metadata.
"""

import json
import logging
import re

from research.services.scout_service import find_json_object

logger = logging.getLogger(__name__)

CRITIC_BRIEF = """You are Critic, working under the scholar's standards above.

Pressure-test whether the source genuinely supports the way it is being used — do
not rubber-stamp it. Use the web tools to check the actual source when that helps.
Judge against the standards (e.g. correlation is not cause; do not overstate).

Return ONLY a single JSON object — no prose, no markdown fences — shaped exactly:
{
  "verdict": "holds | overstated | unsupported | inconclusive",
  "reasoning": "1-3 sentences, grounded in the source and the standards",
  "concerns": ["short concern", "..."]
}
- holds: genuinely supports the stated use.
- overstated: relevant, but the claim leans on it harder than it can bear.
- unsupported: does not actually support the stated use.
- inconclusive: cannot tell from what is available."""


def build_critic_instructions(soul_content):
    """Compose the run instructions: the soul file (standards) + the Critic brief."""
    parts = []
    if soul_content and soul_content.strip():
        parts.append("# Research standards (soul file)\n" + soul_content.strip())
    parts.append(CRITIC_BRIEF)
    return "\n\n".join(parts)


def critic_input(item):
    """The source under review, framed as the Critic's task."""
    return (
        f"Source: {item.title}\n"
        f"URL: {item.url}\n"
        f"Staged as: {item.evidence_label or 'unlabelled'}\n"
        f"Claimed relevance: {item.rationale}\n"
        f"Citation context: {item.citation_context}\n\n"
        "Pressure-test whether this source genuinely supports that use, under the "
        "standards."
    )


VALID_VERDICTS = {"holds", "overstated", "unsupported", "inconclusive"}


def parse_critic_verdict(output):
    """Extract the verdict object from the Critic's output, tolerating stray text."""
    if not output:
        return None
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    data = find_json_object(text, required_key="verdict")
    if not isinstance(data, dict):
        logger.warning("Critic output was not JSON-parseable")
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    concerns = data.get("concerns")
    return {
        "verdict": verdict if verdict in VALID_VERDICTS else "inconclusive",
        "reasoning": str(data.get("reasoning") or ""),
        "concerns": (
            [str(c) for c in concerns if c] if isinstance(concerns, list) else []
        ),
    }
