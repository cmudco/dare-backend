"""Query analysis stage (audit mistake #4).

A fast, cheap LLM turns a raw question into a structured QueryPlan — intent
(which gates conditional MMR), exact keywords (for the BM25 leg), a cleaned
rewrite, and a HyDE passage. Any failure returns ``None`` so retrieval always
proceeds on the raw query.
"""

import json
import logging
from typing import Optional

import anthropic

from conversations.constants import Provider
from core.services.api_key_service import get_provider_api_key_sync
from core.services.rag.config import setting
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
    """Raw query -> QueryPlan, via a structured LLM call.

    Credentials come from ``get_provider_api_key_sync`` — the same
    database-first, env-fallback resolution every other Claude call in DARE
    uses. Letting the SDK find its own key would make this stage depend on
    ``ANTHROPIC_API_KEY`` alone, so it would sit dead in any environment that
    configures Claude the normal way (admin key, or ``CLAUDE_API_KEY``).

    A pipeline builds one analyzer per retrieval, so ``last_error`` describes
    the most recent ``analyze`` call and is safe to read straight after it.
    """

    def __init__(self) -> None:
        self.last_error: Optional[str] = None

    def use_hyde(self) -> bool:
        """Advanced RAG always feeds the rewritten/HyDE text into retrieval."""
        return True

    def analyze(self, query: str) -> Optional[QueryPlan]:
        self.last_error = None
        if not query:
            return None
        try:
            client = anthropic.Anthropic(
                api_key=get_provider_api_key_sync(Provider.CLAUDE.value)
            )
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
            self.last_error = str(exc)
            return None

    def _call(self, client, model: str, query: str) -> dict:
        """Ask for the plan under a schema the API enforces.

        ``output_config`` goes through ``extra_body`` because the pinned SDK
        has no typed argument for it — passing it directly raises TypeError
        client-side, before any request is made. The wire field is real and
        the API enforces it; only the Python signature lags. ``ClaudeService``
        sends it the same way.

        A model that rejects the field returns 400, which ``analyze`` already
        turns into a raw-query retrieval.
        """
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{"role": "user", "content": query}],
            extra_body={
                "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}}
            },
        )
        return json.loads(next(b.text for b in response.content if b.type == "text"))
