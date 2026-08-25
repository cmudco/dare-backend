"""Reference pricing for models DARE routes but does not bill for.

A LiteLLM-routed call is paid for on the user's own proxy account, so DARE
records it at ``amount=0`` and never touches the wallet. That leaves Cost
Tracking with tokens but no monetary figure at all, which understates usage
that is real even when DARE isn't the one charging for it.

Rates come from one of two places, in order:

1. an ``LLM`` row, when the proxy is serving a model DARE also offers — that
   row is admin-editable and authoritative;
2. ``model_prices.json``, for models DARE routes but does not itself offer.

Anything matching neither stays unpriced. Inventing a rate would put a wrong
number in a billing table, which is worse than showing none.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

from conversations.models import LLM
from core.services.model_identity import pricing_keys

logger = logging.getLogger(__name__)

_PRICES_PATH = Path(__file__).with_name("model_prices.json")


@dataclass(frozen=True)
class ReferenceRates:
    """Per-million token rates from the registry.

    Field names deliberately mirror ``LLM`` so both sources satisfy the same
    read shape and the cost formula keeps a single owner in BillingService.
    """

    input_token_rate_per_million: Decimal
    output_token_rate_per_million: Decimal


def _load_registry() -> Dict[str, ReferenceRates]:
    try:
        payload = json.loads(_PRICES_PATH.read_text())
    except (OSError, ValueError):
        logger.exception(
            "Could not read %s; registry pricing unavailable.", _PRICES_PATH
        )
        return {}

    registry = {}
    for key, rates in (payload.get("prices") or {}).items():
        try:
            registry[key.strip().lower()] = ReferenceRates(
                input_token_rate_per_million=Decimal(str(rates["input"])),
                output_token_rate_per_million=Decimal(str(rates["output"])),
            )
        except (KeyError, TypeError, ArithmeticError):
            logger.warning("Skipping malformed price entry for %s.", key)
    return registry


REGISTRY = _load_registry()


def _find_llm_row(wanted) -> Optional[LLM]:
    """The DARE-side model a proxy identifier refers to, if any.

    An exact identifier match wins before a normalized one, and rows are read
    in a fixed order, so a model can never be priced from a sibling that
    happens to be seeded first.
    """
    rows = list(LLM.objects.exclude(identifier="").order_by("pk"))
    for llm in rows:
        if llm.identifier.strip().lower() in wanted:
            return llm
    for llm in rows:
        if wanted.intersection(pricing_keys(llm.identifier)):
            return llm
    return None


def reference_rates(model_name: str):
    """Rates to price a proxy-routed call with, or None when unknown."""
    wanted = set(pricing_keys(model_name))
    if not wanted:
        return None

    row = _find_llm_row(wanted)
    if row is not None:
        return row

    for key in wanted:
        rates = REGISTRY.get(key)
        if rates is not None:
            return rates

    logger.info(
        "No rates for proxy identifier %s in either the model table or the "
        "price registry; recording usage without a reference cost.",
        model_name,
    )
    return None
