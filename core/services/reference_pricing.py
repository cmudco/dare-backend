"""Reference pricing for models DARE routes but does not bill for.

A LiteLLM-routed call is paid for on the user's own proxy account, so DARE
records it at ``amount=0`` and never touches the wallet. That leaves Cost
Tracking with tokens but no monetary figure at all, which understates usage
that is real even when DARE isn't the one charging for it.

Rates come solely from ``model_prices.json``, synced from LiteLLM's published
price file. That file is the source of truth for what a proxy route costs: it
is keyed by the identifiers a gateway actually serves, so a model id matches
verbatim, and it prices each route separately.

The model table is deliberately not consulted. It holds one rate per model —
what DARE charges to run that model on its own keys — which is a different
question and cannot express route. claude-sonnet-5 costs $2.00 direct, $2.20
through a US region, and $3.00 on DARE's own wallet; only the registry knows
which of those a given call actually was.

A model the registry does not carry stays unpriced. Inventing a rate, or
borrowing DARE's own, would put a wrong number in a billing table.

The registry is a module-level dict read once at import, so pricing a call is
one dictionary lookup and no query at all.
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict

from core.services.model_identity import pricing_keys

logger = logging.getLogger(__name__)

PRICES_PATH = Path(__file__).with_name("model_prices.json")


@dataclass(frozen=True)
class ReferenceRates:
    """Per-million token rates from the registry.

    Field names mirror ``LLM`` so BillingService can price from either shape
    and the cost formula keeps a single owner there.
    """

    input_token_rate_per_million: Decimal
    output_token_rate_per_million: Decimal
    cached_input_token_rate_per_million: Optional[Decimal] = None


def _load_registry() -> Dict[str, ReferenceRates]:
    try:
        payload = json.loads(PRICES_PATH.read_text())
    except (OSError, ValueError):
        logger.exception(
            "Could not read %s; registry pricing unavailable.", PRICES_PATH
        )
        return {}

    registry = {}
    for key, rates in (payload.get("prices") or {}).items():
        try:
            registry[key.strip().lower()] = ReferenceRates(
                input_token_rate_per_million=Decimal(str(rates["input"])),
                output_token_rate_per_million=Decimal(str(rates["output"])),
                cached_input_token_rate_per_million=(
                    Decimal(str(rates["cached_input"]))
                    if rates.get("cached_input") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ArithmeticError):
            logger.warning("Skipping malformed price entry for %s.", key)
    return registry


REGISTRY = _load_registry()


def reference_rates(model_name: str):
    """Rates to price a proxy-routed call with, or None when unknown."""
    exact = (model_name or "").strip().lower()
    if not exact:
        return None

    # The overwhelmingly common path: the gateway serves the identifier the
    # registry is keyed by, so this resolves without touching the database.
    rates = REGISTRY.get(exact)
    if rates is not None:
        return rates

    wanted = set(pricing_keys(model_name))
    for key in wanted:
        rates = REGISTRY.get(key)
        if rates is not None:
            return rates

    logger.info(
        "No rates for proxy identifier %s in the price registry; recording "
        "usage without a reference cost. Re-run sync_model_prices if the "
        "gateway has started serving new models.",
        model_name,
    )
    return None
