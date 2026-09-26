"""Pure decisions for the two per-user ceilings a group can set.

- The refill cap bounds how high scheduled refills lift a DARE wallet.
- The LiteLLM spend limit bounds how much a member may spend through their
  group's gateway key, which DARE never debits and so cannot stop by balance.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

ZERO = Decimal("0")


def refill_credit(amount: Decimal, cap: Optional[Decimal], balance: Decimal) -> Decimal:
    """What a scheduled refill should add: the full amount, or up to the cap."""
    if cap is None:
        return amount
    return max(min(amount, cap - balance), ZERO)


@dataclass(frozen=True)
class SpendLimit:
    """A member's LiteLLM spend against the limit that applies to them."""

    limit: Decimal
    used: Decimal
    source: str  # PolicySourceChoice value: USER or GROUP

    @property
    def is_reached(self) -> bool:
        return self.used >= self.limit
