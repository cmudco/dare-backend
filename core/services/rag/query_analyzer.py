"""Query analysis stage (audit mistake #4).

A fast, cheap LLM turns a raw question into a structured QueryPlan — intent
(which gates conditional MMR), exact keywords (for the BM25 leg), a cleaned
rewrite, and a HyDE passage. Opt-in via ``RAG_QUERY_ANALYSIS_ENABLED``; returns
``None`` on disable or any error so retrieval always proceeds on the raw query.
"""

import json
import logging
from typing import Optional

import anthropic

from core.services.rag.config import bool_flag, setting
from core.services.rag.dtos import QueryPlan

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


class QueryAnalyzer:
    """Raw query -> QueryPlan, via a structured LLM call."""

    def is_enabled(self) -> bool:
        return bool_flag("RAG_QUERY_ANALYSIS_ENABLED")

    def use_hyde(self) -> bool:
        """Whether the rewritten/HyDE text should feed retrieval (opt-in, A/B first)."""
        return bool_flag("RAG_HYDE_ENABLED")

    def analyze(self, query: str) -> Optional[QueryPlan]:
        if not self.is_enabled() or not query:
            return None
        try:
            client = anthropic.Anthropic()
            model = setting("RAG_QUERY_ANALYSIS_MODEL", DEFAULT_MODEL)
            data = self._call(client, model, query)
            return QueryPlan(
                intent=data.get("intent", "precise_lookup"),
                keywords=tuple(data.get("keywords", [])),
                rewritten_query=data.get("rewritten_query", ""),
                hyde_passage=data.get("hyde_passage", ""),
            )
        except Exception as exc:  # never let analysis break retrieval
            logger.warning("Query analysis failed; using raw query: %s", exc)
            return None

    def _call(self, client, model: str, query: str) -> dict:
        """Structured output where supported; strict tool-use on older SDKs."""
        messages = [{"role": "user", "content": query}]
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=_SYSTEM,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            return json.loads(
                next(b.text for b in response.content if b.type == "text")
            )
        except TypeError:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=_SYSTEM,
                messages=messages,
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
