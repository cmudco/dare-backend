"""
Billing-specific exceptions.

These exist so callers can distinguish a billing/payment failure from a generic
``ValidationError`` and decide how to react (halt the workflow, surface a
402 to the client, retry on a fallback wallet, etc.). Rolled out as part of
the wallet hardening track to remove silent partial-debit failure modes.
"""
from django.utils.translation import gettext_lazy as _


class PaymentRequiredError(Exception):
    """
    Raised when an action cannot be billed because the resolved wallet does
    not have sufficient balance.

    Attributes:
        code: Discriminated machine-readable code (e.g. ``OWNER_WALLET_EMPTY``,
            ``GROUP_WALLET_EMPTY``, ``BOT_CAP_REACHED``, ``LITELLM_UNAVAILABLE``,
            ``INSUFFICIENT_BALANCE`` for the generic case).
        details: Optional dict with current_balance / required_amount / etc.
    """

    DEFAULT_CODE = 'INSUFFICIENT_BALANCE'

    def __init__(self, message=None, *, code=None, details=None):
        self.code = code or self.DEFAULT_CODE
        self.details = details or {}
        super().__init__(message or _('Insufficient balance to complete the request.'))


class BotModelUnavailable(Exception):
    """A bot's configured model cannot be served for this turn.

    Raised when the model is not the one the bot was saved with, or when the
    bot owner can no longer use the LiteLLM key it routes through (deleted,
    expired, or the owner left the key's group). Distinct from a payment
    failure: no wallet top-up fixes it, only the owner re-picking a model.
    """

    code = "BOT_MODEL_UNAVAILABLE"

    def __init__(self, message=None):
        super().__init__(
            message
            or _(
                "This bot's AI model is no longer available. Ask the bot's owner "
                "to choose a new model."
            )
        )
