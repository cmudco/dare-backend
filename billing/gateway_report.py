"""The LiteLLM gateway's own account of a key's spend.

Gateway keys may only call model routes, so DARE cannot query spend. Every
model response does, however, carry the key's running total and budget in
``x-litellm-key-*`` headers. Recording them gives admins the gateway's figure
next to DARE's estimate without a master key.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from django.db.models import F, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from billing.models import LiteLLMKey

logger = logging.getLogger(__name__)

KEY_SPEND_HEADER = "x-litellm-key-spend"
KEY_MAX_BUDGET_HEADER = "x-litellm-key-max-budget"


@dataclass(frozen=True)
class GatewayKeyReport:
    spend: Decimal
    max_budget: Optional[Decimal]


def gateway_user_id(user) -> str:
    """Stable, non-identifying end-user id the gateway attributes spend to."""
    return f"dare-user-{user.pk}"


def parse_gateway_key_report(headers: Mapping[str, str]) -> Optional[GatewayKeyReport]:
    """Read the key totals from a gateway response, or None if it sent none."""
    spend = _decimal(headers.get(KEY_SPEND_HEADER))
    if spend is None:
        return None
    return GatewayKeyReport(
        spend=spend, max_budget=_decimal(headers.get(KEY_MAX_BUDGET_HEADER))
    )


def record_gateway_key_report(key_id: str, report: GatewayKeyReport) -> None:
    # Greatest: gateway replicas cache the key row, so a later response can
    # report an older, lower total. Spend on a one-off budget never falls.
    LiteLLMKey.objects.filter(pk=key_id).update(
        gateway_spend=Greatest(
            Coalesce(F("gateway_spend"), Value(report.spend)), Value(report.spend)
        ),
        gateway_max_budget=report.max_budget,
        gateway_reported_at=timezone.now(),
    )


def _decimal(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None or raw in ("", "None"):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        logger.warning("Ignoring unparseable LiteLLM gateway header value %r", raw)
        return None
    return value if value.is_finite() else None
