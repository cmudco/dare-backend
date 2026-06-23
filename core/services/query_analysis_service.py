"""Query analysis (Track A, mistake #4) — understand the query before retrieving.

A fast, cheap LLM turns a raw user question into a structured retrieval plan:
  intent          precise_lookup | exploratory | comparison   -> gates conditional MMR
  keywords        exact terms (names, IDs, codes)             -> strengthens the BM25 leg
  rewritten_query cleaned, disambiguated query                -> cleaner dense embedding
  hyde_passage    a hypothetical answer passage               -> HyDE dense retrieval

Opt-in: disabled unless ``RAG_QUERY_ANALYSIS_ENABLED`` is set. Returns ``None`` on
any failure so retrieval always proceeds with the raw query.
"""

import json
import logging
import os
from typing import Dict, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"

_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["precise_lookup", "exploratory", "comparison"],
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "rewritten_query": {"type": "string"},
        "hyde_passage": {"type": "string"},
    },
    "required": ["intent", "keywords", "rewritten_query", "hyde_passage"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the query-analysis stage of a retrieval pipeline. For each user query "
    "return: intent ('precise_lookup' for a specific record/identifier/person, "
    "'exploratory' for how/why or broad-evidence questions, 'comparison' for "
    "contrasts); keywords (the exact tokens a keyword index should match — names, "
    "identifiers, numbers, places — no stopwords); rewritten_query (a cleaned, "
    "disambiguated restatement for semantic search); hyde_passage (one or two "
    "sentences of a plausible answer, to embed instead of the bare question)."
)


def is_enabled() -> bool:
    if getattr(settings, "RAG_QUERY_ANALYSIS_ENABLED", False):
        return True
    return os.environ.get("RAG_QUERY_ANALYSIS_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    )


def use_hyde() -> bool:
    """Whether to feed the rewritten/HyDE text into retrieval (opt-in, A/B first)."""
    if getattr(settings, "RAG_HYDE_ENABLED", False):
        return True
    return os.environ.get("RAG_HYDE_ENABLED", "").lower() in ("1", "true", "yes")


def _model() -> str:
    return (
        getattr(settings, "RAG_QUERY_ANALYSIS_MODEL", None)
        or os.environ.get("RAG_QUERY_ANALYSIS_MODEL")
        or DEFAULT_MODEL
    )


def analyze_query(query: str) -> Optional[Dict]:
    """Return a structured retrieval plan, or ``None`` if disabled or on any error."""
    if not is_enabled() or not query:
        return None
    try:
        import anthropic  # lazy: only imported when query analysis is enabled

        client = anthropic.Anthropic()
        try:
            # Preferred: structured-output API (newer SDKs).
            response = client.messages.create(
                model=_model(),
                max_tokens=512,
                system=_SYSTEM,
                messages=[{"role": "user", "content": query}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text)
        except TypeError:
            # Fallback for SDKs without output_config: forced strict tool use.
            response = client.messages.create(
                model=_model(),
                max_tokens=512,
                system=_SYSTEM,
                messages=[{"role": "user", "content": query}],
                tools=[
                    {
                        "name": "plan",
                        "description": "Return the structured retrieval plan.",
                        "input_schema": _SCHEMA,
                    }
                ],
                tool_choice={"type": "tool", "name": "plan"},
            )
            return next(b.input for b in response.content if b.type == "tool_use")
    except Exception as exc:  # never let analysis break retrieval
        logger.warning("Query analysis failed; using raw query: %s", exc)
        return None
