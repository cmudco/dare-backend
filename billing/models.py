from django.db import models, transaction as db_transaction
from billing.constants import TransactionTypeChoice
from common.models import TimeStampMixin
from users.models import User
from pydantic import ValidationError

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
    balance = models.IntegerField(
        default=5,
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
    amount = models.IntegerField(
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
        """
        Returns the amount formatted as USD.
        """
        return f"${self.amount / 100:.2f}" if self.amount else ("No amount")

    def save(self, *args, **kwargs):
        """
        Override save to handle balance deduction for debit transactions.
        """
        if self.pk is None:
            with db_transaction.atomic():
                if self.type == TransactionTypeChoice.DEBIT:
                    wallet = Wallet.objects.get(user=self.user)
                    if wallet.balance < self.amount:
                        raise ValidationError(("Insufficient wallet balance"))
                    wallet.balance -= self.amount
                elif self.type == TransactionTypeChoice.CREDIT:
                    wallet.balance += self.amount
                wallet.save()
        super().save(*args, **kwargs)

    def __str__(self):
        """
        Returns a string representation of the transaction.
        """
        return f"{self.user.email}: {self.get_type_display()} - {self.display_amount}"
