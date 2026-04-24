import csv
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from billing.constants import (
    PolicySourceChoice,
    TransactionSourceChoice,
    TransactionTypeChoice,
)
from billing.models import (
    GroupWallet,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    Wallet,
)


@dataclass(frozen=True)
class EffectiveRefillPolicy:
    """
    Resolved refill amount and period for a single user, with the tier that
    each value ultimately came from. Produced by WalletService.get_effective_refill_policy.
    """
    amount: Decimal
    period_days: int
    amount_source: str   # PolicySourceChoice value
    period_source: str   # PolicySourceChoice value


class TransactionExportService:
    """
    Service class for exporting transactions to CSV format.
    """

    @staticmethod
    def export_to_csv(queryset, filename=None):
        """
        Export a queryset of transactions to CSV format.

        Args:
            queryset: QuerySet of Transaction objects
            filename: Optional custom filename (defaults to transaction-history-YYYY-MM-DD.csv)

        Returns:
            HttpResponse with CSV content
        """
        timestamp = timezone.now().strftime('%Y-%m-%d')
        if filename is None:
            filename = f'transaction-history-{timestamp}.csv'

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)

        writer.writerow([
            'User Email',
            'Amount',
            'Type',
            'Source',
            'Message',
            'LLM',
            'Input Tokens',
            'Output Tokens',
            'Billing Mode',
            'Platform',
            'Related Group',
            'Date',
        ])

        optimized_queryset = queryset.select_related('user', 'llm', 'related_group')

        for txn in optimized_queryset:
            writer.writerow([
                txn.user.email,
                txn.display_amount,
                txn.get_type_display(),
                txn.get_source_display(),
                txn.message or '',
                txn.llm_name or 'N/A',
                txn.input_tokens if txn.input_tokens is not None else 'N/A',
                txn.output_tokens if txn.output_tokens is not None else 'N/A',
                txn.get_billing_mode_display(),
                txn.get_platform_display(),
                txn.related_group.access_code if txn.related_group else 'N/A',
                txn.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            ])

        return response

    @staticmethod
    def export_user_transactions(user, platform=None, start_date=None, end_date=None, filename=None):
        """
        Export transactions for a specific user with optional filters.
        """
        queryset = Transaction.objects.filter(user=user)

        if platform:
            queryset = queryset.filter(platform=platform)
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)

        queryset = queryset.order_by('-created_at')
        return TransactionExportService.export_to_csv(queryset, filename)


class WalletService:
    """
    Service class for wallet operations and refill-policy resolution.
    """

    # --- Refill policy resolution -----------------------------------------

    @staticmethod
    def get_effective_refill_policy(user) -> EffectiveRefillPolicy:
        """
        Resolve the user's effective refill amount and period using the 3-tier
        hierarchy with field-level fallthrough:

            user.refill_override.refill_amount      -> group.group_wallet.refill_amount      -> system
            user.refill_override.refill_period_days -> group.group_wallet.refill_period_days -> system
        """
        system_policy = SystemRefillPolicy.load()
        amount: Optional[Decimal] = None
        period: Optional[int] = None
        amount_source = PolicySourceChoice.SYSTEM
        period_source = PolicySourceChoice.SYSTEM

        override = getattr(user, "refill_override", None) or (
            UserRefillOverride.objects.filter(user=user).first()
        )
        if override:
            if override.refill_amount is not None:
                amount = override.refill_amount
                amount_source = PolicySourceChoice.USER
            if override.refill_period_days is not None:
                period = override.refill_period_days
                period_source = PolicySourceChoice.USER

        if amount is None or period is None:
            group_wallet = WalletService.get_group_wallet_for_user(user)
            if group_wallet is not None:
                if amount is None and group_wallet.refill_amount is not None:
                    amount = group_wallet.refill_amount
                    amount_source = PolicySourceChoice.GROUP
                if period is None and group_wallet.refill_period_days is not None:
                    period = group_wallet.refill_period_days
                    period_source = PolicySourceChoice.GROUP

        if amount is None:
            amount = system_policy.refill_amount
        if period is None:
            period = system_policy.refill_period_days

        return EffectiveRefillPolicy(
            amount=amount,
            period_days=period,
            amount_source=amount_source,
            period_source=period_source,
        )

    @staticmethod
    def is_user_due_for_refill(user, *, now=None) -> bool:
        """
        True iff the wallet has never been refilled OR now - last_refill_at >= effective period.
        """
        now = now or timezone.now()
        try:
            wallet = user.wallet
        except Wallet.DoesNotExist:
            return False

        if wallet.last_refill_at is None:
            return True

        policy = WalletService.get_effective_refill_policy(user)
        return (now - wallet.last_refill_at) >= timedelta(days=policy.period_days)

    @staticmethod
    def get_group_wallet_for_user(user) -> Optional[GroupWallet]:
        """Return the GroupWallet of the user's access code group, or None.

        Uses attribute access so callers that prefetch
        `access_code_group__group_wallet` incur zero extra queries; otherwise
        falls back to a filtered lookup.
        """
        group = getattr(user, "access_code_group", None)
        if group is None:
            return None
        return getattr(group, "group_wallet", None) or (
            GroupWallet.objects.filter(group=group).first()
        )

    # --- Top-ups ----------------------------------------------------------

    @staticmethod
    def add_topup(
        user,
        amount: Decimal = Decimal("5.00"),
        message: str = "Monthly $5 top-up",
        source: str = TransactionSourceChoice.SCHEDULED_REFILL,
        related_group=None,
    ):
        """Create a CREDIT transaction on the user's wallet."""
        with transaction.atomic():
            txn = Transaction.objects.create(
                user=user,
                amount=amount,
                type=TransactionTypeChoice.CREDIT,
                message=message,
                source=source,
                related_group=related_group,
            )
            if source == TransactionSourceChoice.SCHEDULED_REFILL:
                Wallet.objects.filter(user=user).update(last_refill_at=timezone.now())
            return txn

    @staticmethod
    def has_recent_topup(user) -> bool:
        """
        True if the user's effective refill period has not yet elapsed since their last refill.
        Replaces the old message-string-based check — now driven by Wallet.last_refill_at.
        """
        return not WalletService.is_user_due_for_refill(user)

    @staticmethod
    def is_eligible_for_topup(user):
        """
        Check if user is eligible for a top-up.
        Criteria:
        - User must be active
        - Wallet must exist
        - Enough time must have elapsed since their last refill (per effective policy)
        """
        if not user.is_active:
            return False, "User is not active"

        try:
            _wallet = user.wallet
        except Wallet.DoesNotExist:
            return False, "User has no wallet"

        if not WalletService.is_user_due_for_refill(user):
            return False, "User already received a refill within their current period"

        return True, "User is eligible for top-up"

    @staticmethod
    def get_last_topup_date(user):
        """Return the timestamp of the user's last scheduled refill, or None."""
        try:
            wallet = user.wallet
        except Wallet.DoesNotExist:
            return None
        return wallet.last_refill_at

    @staticmethod
    def get_next_topup_date(user):
        """Return the datetime at which the user will next become eligible."""
        policy = WalletService.get_effective_refill_policy(user)
        last = WalletService.get_last_topup_date(user)
        if last is None:
            try:
                last = user.wallet.created_at
            except Wallet.DoesNotExist:
                return None
        return last + timedelta(days=policy.period_days)

    @staticmethod
    def debit_wallet(user, amount, message="", llm=None, input_tokens=0, output_tokens=0):
        """Debit amount from user's wallet."""
        with transaction.atomic():
            return Transaction.objects.create(
                user=user,
                amount=amount,
                type=TransactionTypeChoice.DEBIT,
                message=message,
                llm=llm,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                source=TransactionSourceChoice.USAGE,
            )
