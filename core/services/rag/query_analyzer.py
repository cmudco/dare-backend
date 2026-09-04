"""Build an optional retrieval plan from a user's question."""

import logging
from typing import Literal, Optional

from asgiref.sync import async_to_sync
from pydantic import BaseModel

from core.services.background_model_service import BackgroundModelService
from core.services.rag.dtos import QueryPlan
from users.models import User

logger = logging.getLogger(__name__)


class QueryPlanResponse(BaseModel):
    intent: Literal["precise_lookup", "exploratory", "comparison"]
    keywords: list[str]
    rewritten_query: str
    hyde_passage: str


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
    """Raw query -> QueryPlan through the user's background model."""

    def __init__(self) -> None:
        self.last_error: Optional[str] = None

    def use_hyde(self) -> bool:
        """Advanced RAG always feeds the rewritten/HyDE text into retrieval."""
        return True

    def analyze(
        self,
        query: str,
        payer_user_id: Optional[int] = None,
        payer_bot_id: Optional[int] = None,
    ) -> Optional[QueryPlan]:
        self.last_error = None
        if not query:
            return None
        if payer_user_id is None and payer_bot_id is None:
            self.last_error = "Query analysis requires a billing user"
            return None
        try:
            user = (
                User.objects.get(pk=payer_user_id)
                if payer_user_id is not None
                else None
            )
            result = async_to_sync(BackgroundModelService().parse_structured)(
                user=user,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": query},
                ],
                response_model=QueryPlanResponse,
                description="Advanced RAG query analysis",
                max_tokens=512,
                public_bot_id=payer_bot_id,
            )
            data = result.value
            return QueryPlan(
                intent=data.intent,
                keywords=tuple(data.keywords),
                rewritten_query=data.rewritten_query,
                hyde_passage=data.hyde_passage,
            )
        except Exception as exc:  # never let analysis break retrieval
            logger.warning("Query analysis failed; using raw query: %s", exc)
            self.last_error = str(exc)
            return None
