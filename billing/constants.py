from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "billing"


class TransactionTypeChoice(models.IntegerChoices):
    DEBIT = 1, _("Debit")
    CREDIT = 2, _("Credit")


class TransactionSourceChoice(models.TextChoices):
    SCHEDULED_REFILL = "SCHEDULED_REFILL", _("Scheduled refill")
    GROUP_ALLOCATION = "GROUP_ALLOCATION", _("Group pool allocation")
    GROUP_BUDGET_TOPUP = "GROUP_BUDGET_TOPUP", _("Group budget top-up")
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT", _("Admin adjustment")
    REGISTRATION = "REGISTRATION", _("Registration credit")
    USAGE = "USAGE", _("LLM usage debit")
    OTHER = "OTHER", _("Other")


class PolicySourceChoice(models.TextChoices):
    """Where the effective refill policy for a user came from."""
    USER = "USER", _("User override")
    GROUP = "GROUP", _("Group policy")
    SYSTEM = "SYSTEM", _("System default")


class LiteLLMKeySourceChoice(models.TextChoices):
    """Where a LiteLLMKey row originated from."""
    USER = "USER", _("User self-served")
    ADMIN_USER = "ADMIN_USER", _("Admin issued to user")
    ADMIN_GROUP = "ADMIN_GROUP", _("Admin issued to group")


class UserWalletPreferenceTypeChoice(models.TextChoices):
    """Active wallet type a user has selected for routing LLM calls."""
    DARE = "DARE", _("DARE Wallet")
    BYO = "BYO", _("BYO Key")
    LITELLM = "LITELLM", _("LiteLLM Key")


DEFAULT_REFILL_AMOUNT = "5.00"
DEFAULT_REFILL_PERIOD_DAYS = 30
