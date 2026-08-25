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
from core.services.model_identity import candidate_keys, normalize_identifier

logger = logging.getLogger(__name__)


def find_reference_llm(model_name: str) -> Optional[LLM]:
    """Return the DARE-side model a proxy identifier refers to, if any."""
    wanted = set(candidate_keys(model_name))
    if not wanted:
        return None

    for llm in LLM.objects.exclude(identifier=""):
        if normalize_identifier(llm.identifier) in wanted:
            return llm

    logger.info(
        "No DARE-side model matches proxy identifier %s; "
        "recording usage without a reference cost.",
        model_name,
    )
    return None
