"""Reference pricing for models DARE routes but does not bill for.

A LiteLLM-routed call is paid for on the user's own proxy account, so DARE
records it at ``amount=0`` and never touches the wallet. That leaves Cost
Tracking with tokens but no monetary figure at all, which understates usage
that is real even when DARE isn't the one charging for it.

When the proxy is serving a model DARE also offers directly, that model's row
already carries authoritative rates. This module finds it, so billing can
report what the same call would have cost at DARE's prices without changing
what the user is charged.
"""

import logging
from typing import Optional

from conversations.models import LLM
from core.services.model_identity import pricing_keys

logger = logging.getLogger(__name__)


def find_reference_llm(model_name: str) -> Optional[LLM]:
    """Return the DARE-side model a proxy identifier refers to, if any.

    An exact identifier match wins before a normalized one, and rows are read
    in a fixed order, so a model can never be priced from a sibling that
    happens to be seeded first.
    """
    wanted = set(pricing_keys(model_name))
    if not wanted:
        return None

    rows = list(LLM.objects.exclude(identifier="").order_by("pk"))
    for llm in rows:
        if llm.identifier.strip().lower() in wanted:
            return llm
    for llm in rows:
        if wanted.intersection(pricing_keys(llm.identifier)):
            return llm

    logger.info(
        "No DARE-side model matches proxy identifier %s; "
        "recording usage without a reference cost.",
        model_name,
    )
    return None
