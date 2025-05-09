from decimal import Decimal
from django.db import models, transaction as db_transaction
from django.core.exceptions import ValidationError
from billing.constants import TransactionTypeChoice
from common.models import TimeStampMixin
from users.models import User

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
    amount = models.DecimalField(
        max_digits=15,
        decimal_places=15,
        verbose_name=("Amount"),
        help_text=("Transaction amount in USD"),
    )
    type = models.IntegerField(
        choices = TransactionTypeChoice.choices,
        verbose_name=("Transaction Type"),
        help_text=("Type of the transaction: debit or credit"),
    )
    class Meta:
        verbose_name = ("Transaction")
        verbose_name_plural = ("Transactions")

    @property
    def display_amount(self):
        if self.amount is None:
            return "No amount"
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
        Override save to handle balance deduction for debit transactions.
        """
        if self.pk is None:
            try:
                with db_transaction.atomic():
                    wallet = Wallet.objects.get(user=self.user)
                    if self.type == TransactionTypeChoice.DEBIT:
                        if wallet.balance < self.amount:
                            raise ValidationError({
                                "error": "insufficient_balance",
                                "message": "Insufficient wallet balance",
                                "current_balance": str(wallet.balance),
                                "required_amount": str(self.amount)
                            })
                        wallet.balance -= self.amount
                    elif self.type == TransactionTypeChoice.CREDIT:
                        wallet.balance += self.amount
                    wallet.save()
            except Exception as e:
                raise
        super().save(*args, **kwargs)

    def __str__(self):
        """
        Returns a string representation of the transaction.
        """
        return f"{self.user.email}: {self.get_type_display()} - {self.display_amount}"
