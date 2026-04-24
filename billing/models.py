from decimal import Decimal
from django.db import models, transaction as db_transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from billing.constants import (
    TransactionTypeChoice,
    TransactionSourceChoice,
    DEFAULT_REFILL_AMOUNT,
    DEFAULT_REFILL_PERIOD_DAYS,
)
from common.models import TimeStampMixin
from conversations.models import LLM
from users.models import User, AccessCodeGroup
from users.constants import AuthSourceChoice
from api_keys.constants import BillingModeChoice


class SystemRefillPolicy(TimeStampMixin):
    """
    Singleton holding the platform-wide default refill amount and period.
    Edited via Django admin; seeded with $5 / 30 days to preserve existing behaviour.
    """
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal(DEFAULT_REFILL_AMOUNT),
        verbose_name=_("Default Refill Amount (USD)"),
        help_text=_("Platform-wide default refill amount applied to every user whose group/override does not specify one."),
    )
    refill_period_days = models.PositiveIntegerField(
        default=DEFAULT_REFILL_PERIOD_DAYS,
        verbose_name=_("Default Refill Period (days)"),
        help_text=_("Platform-wide default number of days between automatic refills."),
    )

    class Meta:
        verbose_name = _("System Refill Policy")
        verbose_name_plural = _("System Refill Policy")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the singleton, creating it with defaults if absent."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"System refill: ${self.refill_amount} every {self.refill_period_days} day(s)"


class GroupWallet(TimeStampMixin):
    """
    Per-group wallet holding the budget a group owner manages, plus optional
    group-level overrides of refill amount / period. Member refills debit
    `budget_balance`; when it hits zero, refills pause until refunded.
    """
    group = models.OneToOneField(
        AccessCodeGroup,
        on_delete=models.CASCADE,
        related_name="group_wallet",
        verbose_name=_("Access Code Group"),
        help_text=_("The access code group this wallet configuration belongs to."),
    )
    budget_balance = models.DecimalField(
        max_digits=15,
        decimal_places=6,
        default=Decimal("0.00"),
        verbose_name=_("Budget Balance (USD)"),
        help_text=_(
            "Budget assigned to this group. Drained by scheduled refills and one-off "
            "allocations to members. Refills pause when this reaches zero."
        ),
    )
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("Group Refill Amount (USD)"),
        help_text=_("Per-member refill amount for this group. Null means inherit the system default."),
    )
    refill_period_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Group Refill Period (days)"),
        help_text=_("Days between automatic refills for members of this group. Null means inherit the system default."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        help_text=_("When inactive, scheduled refills are paused for all members of this group."),
    )

    class Meta:
        verbose_name = _("Group Wallet")
        verbose_name_plural = _("Group Wallets")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})
        if self.budget_balance is not None and self.budget_balance < 0:
            raise ValidationError({"budget_balance": _("Budget balance cannot be negative.")})

    @property
    def display_budget(self):
        return f"${self.budget_balance:.2f}" if self.budget_balance is not None else "$0.00"

    def __str__(self):
        return f"GroupWallet<{self.group.access_code}> budget={self.display_budget}"


class UserRefillOverride(TimeStampMixin):
    """
    Per-user override of refill amount and/or period. Either field may be null
    to fall through to the group's value (or the system default). Set by admins
    platform-wide or by group owners for members of their own group.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="refill_override",
        verbose_name=_("User"),
        help_text=_("The user whose refill policy is being overridden."),
    )
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("Refill Amount (USD)"),
        help_text=_("Custom refill amount for this user. Null means inherit from group/system."),
    )
    refill_period_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Refill Period (days)"),
        help_text=_("Custom period between refills for this user. Null means inherit from group/system."),
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Reason"),
        help_text=_("Audit note explaining why this override exists."),
    )
    set_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refill_overrides_set",
        verbose_name=_("Set By"),
        help_text=_("Admin or group owner who created or last updated this override."),
    )

    class Meta:
        verbose_name = _("User Refill Override")
        verbose_name_plural = _("User Refill Overrides")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})

    def __str__(self):
        parts = []
        if self.refill_amount is not None:
            parts.append(f"${self.refill_amount}")
        if self.refill_period_days is not None:
            parts.append(f"{self.refill_period_days}d")
        detail = "/".join(parts) if parts else "inherit"
        return f"Override<{self.user.email}: {detail}>"


class Wallet(TimeStampMixin):
    """
    Model for user wallets.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="wallet",
        verbose_name=("User"),
        help_text=("The user associated with this wallet"),
    )
    balance = models.DecimalField(
        max_digits=15,
        decimal_places=6,
        default=Decimal("5.00"),
        verbose_name=("Balance"),
        help_text=("Wallet balance in USD"),
    )
    last_refill_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Refill At"),
        help_text=_("Timestamp of the most recent scheduled refill for this user. "
                    "Used by the scheduler to determine when the next refill is due."),
    )

    class Meta:
        verbose_name = ("Wallet")
        verbose_name_plural = ("Wallets")

    @property
    def display_balance(self):
        """
        Returns the balance formatted as USD.
        """
        return f"${self.balance:.2f}" if self.balance else ("No balance")

    def __str__(self):
        """
        Returns a string representation of the wallet.
        """
        return f"Wallet of {self.user.email} with balance {self.display_balance}"

class Transaction(TimeStampMixin):
    """
    Model for transactions in the wallet.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=("User"),
        help_text=("The user associated with this transaction"),
    )
    message = models.TextField(
        blank=True,
        verbose_name=("Message"),
        help_text=("Description of the transaction"),
    )

    llm = models.ForeignKey(
        LLM,
        on_delete=models.SET_NULL,
        related_name="transactions",
        verbose_name=("Model"),
        help_text=("Model used in the transaction"),
        null=True,
        blank=True,
    )
    llm_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=("Model Name"),
        help_text=("Name of the LLM model used (stored for historical reference)"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        verbose_name=("Amount"),
        help_text=("Transaction amount in USD"),
    )
    type = models.IntegerField(
        choices=TransactionTypeChoice.choices,
        verbose_name=("Transaction Type"),
        help_text=("Type of the transaction: debit or credit"),
    )
    source = models.CharField(
        max_length=30,
        choices=TransactionSourceChoice.choices,
        default=TransactionSourceChoice.OTHER,
        verbose_name=_("Source"),
        help_text=_("Origin of this transaction for reporting and auditing."),
    )
    related_group = models.ForeignKey(
        AccessCodeGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("Related Group"),
        help_text=_("Access code group this transaction is associated with, if any."),
    )
    related_transaction = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="related_from",
        verbose_name=_("Related Transaction"),
        help_text=_("Paired transaction — for example, the informational owner row linked to a member's allocation credit."),
    )
    input_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=0,
        verbose_name=("Input Tokens"),
        help_text=("Number of input tokens used in the transaction"),
    )
    output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=0,
        verbose_name=("Output Tokens"),
        help_text=("Number of output tokens used in the transaction"),
    )
    billing_mode = models.CharField(
        max_length=20,
        choices=BillingModeChoice.choices,
        default=BillingModeChoice.WALLET,
        verbose_name=("Billing Mode"),
        help_text=("Billing mode used for this transaction: wallet or own API keys"),
    )
    platform = models.CharField(
        max_length=50,
        choices=AuthSourceChoice.choices,
        default=AuthSourceChoice.DARE,
        verbose_name=("Platform"),
        help_text=("Platform where this transaction originated: DARE or SocraticBots"),
    )

    # Energy/environmental impact tracking
    energy_wh = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Energy (Wh)"),
        help_text=("Estimated energy consumption in Watt-hours"),
    )
    carbon_g = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Carbon (g CO2e)"),
        help_text=("Estimated carbon emissions in grams CO2 equivalent"),
    )
    water_ml = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Water (mL)"),
        help_text=("Estimated water usage in milliliters"),
    )

    class Meta:
        verbose_name = ("Transaction")
        verbose_name_plural = ("Transactions")

    @property
    def display_amount(self):
        if self.amount is None:
            return "No amount"
        if self.amount == Decimal('0'):
            return "$0.00"
        if abs(self.amount) >= Decimal('0.01'):
            return f"${self.amount:.2f}"
        else:
            if abs(self.amount) < Decimal('0.0000001'):
                return f"${self.amount:.8e}"
            else:
                normalized = self.amount.normalize()
                return f"${normalized}"

    def save(self, *args, **kwargs):
        """
        Override save method to handle balance deduction for debit transactions.

        Platform-specific behavior:
        - DARE transactions: Deduct from/add to user's wallet balance
        - SocraticBots transactions: Record only (no wallet impact)
        """
        is_new = self.pk is None

        if is_new:
            if self.llm and not self.llm_name:
                self.llm_name = self.llm.name
            try:
                wallet = self.user.wallet
            except self.user.wallet.RelatedObjectDoesNotExist:
                wallet = Wallet.objects.create(user=self.user, balance=Decimal('5.00'))

            # Only modify wallet balance for DARE platform transactions
            if self.platform == AuthSourceChoice.DARE:
                current_balance = wallet.balance
                if self.type == TransactionTypeChoice.DEBIT:
                    if wallet.balance < self.amount:
                        raise ValidationError({
                            'error': ['insufficient_balance'],
                            'message': ['Insufficient wallet balance'],
                            'current_balance': [str(wallet.balance)],
                            'required_amount': [str(self.amount)]
                        })
                    wallet.balance -= self.amount
                elif self.type == TransactionTypeChoice.CREDIT:
                    wallet.balance += self.amount

                wallet.save(update_fields=['balance'])
            # SocraticBots transactions are recorded but don't affect wallet balance

        super().save(*args, **kwargs)

    def __str__(self):
        """
        Returns a string representation of the transaction.
        """
        token_info = f", {self.input_tokens} input, {self.output_tokens} output tokens" if self.input_tokens is not None and self.output_tokens is not None else ""
        model_info = f" ({self.llm_name})" if self.llm_name else ""
        return f"{self.user.email}: {self.get_type_display()} - {self.display_amount}{model_info}{token_info}"
