"""What a new account receives, whichever way it was created."""

import logging
from decimal import Decimal

from billing.constants import TransactionSourceChoice
from billing.group_wallet import adopt_group_wallet
from billing.models import Wallet
from billing.services import WalletService

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_CREDIT = Decimal("5.00")


def onboard_user(user, code_group=None):
    """Join an access-code group whose slot the caller already took, and grant the starting credit."""
    if code_group:
        user.access_code_group = code_group
        user.platform_role = code_group.default_role
    user.save()

    # Members of a group that was issued a LiteLLM key route through it by
    # default. Their own wallet and credit are untouched: this sets the default
    # provider, not a restriction.
    adopt_group_wallet(user)

    try:
        _grant_initial_credit(user, code_group)
    except Exception:
        # A failed credit must not block the account from being created.
        logger.exception("Failed to apply initial wallet credit for user %s", user.id)


def _grant_initial_credit(user, code_group):
    if getattr(user, "wallet", None) is None:
        Wallet.objects.create(user=user, balance=Decimal("0.00"))

    if code_group and code_group.initial_wallet_credit is not None:
        amount = Decimal(code_group.initial_wallet_credit)
        message = f"Initial credit via access code '{code_group.access_code}'"
    else:
        amount = DEFAULT_INITIAL_CREDIT
        message = "Initial wallet credit"

    if amount > Decimal("0.00"):
        WalletService.add_topup(
            user,
            amount=amount,
            message=message,
            source=TransactionSourceChoice.REGISTRATION,
            related_group=code_group,
        )
