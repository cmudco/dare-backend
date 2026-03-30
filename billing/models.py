from decimal import Decimal
from django.db import models, transaction as db_transaction
from django.core.exceptions import ValidationError
from billing.constants import TransactionTypeChoice
from common.models import TimeStampMixin
from conversations.models import LLM
from users.models import User
from users.constants import AuthSourceChoice
from api_keys.constants import BillingModeChoice

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