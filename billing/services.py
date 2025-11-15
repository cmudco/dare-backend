from decimal import Decimal
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from django.http import HttpResponse
import csv
from .models import Transaction, Wallet
from .constants import TransactionTypeChoice

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
        # Generate timestamp for filename
        timestamp = timezone.now().strftime('%Y-%m-%d')
        if filename is None:
            filename = f'transaction-history-{timestamp}.csv'

        # Create HTTP response with CSV headers
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        # Create CSV writer
        writer = csv.writer(response)

        # Write CSV headers
        writer.writerow([
            'User Email',
            'Amount',
            'Type',
            'Message',
            'LLM',
            'Input Tokens',
            'Output Tokens',
            'Billing Mode',
            'Platform',
            'Date',
        ])

        # Optimize query with select_related
        optimized_queryset = queryset.select_related('user', 'llm')

        # Write transaction data
        for txn in optimized_queryset:
            writer.writerow([
                txn.user.email,
                txn.display_amount,
                txn.get_type_display(),
                txn.message or '',
                txn.llm_name or 'N/A',
                txn.input_tokens if txn.input_tokens is not None else 'N/A',
                txn.output_tokens if txn.output_tokens is not None else 'N/A',
                txn.get_billing_mode_display(),
                txn.get_platform_display(),
                txn.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            ])

        return response

    @staticmethod
    def export_user_transactions(user, platform=None, start_date=None, end_date=None, filename=None):
        """
        Export transactions for a specific user with optional filters.

        Args:
            user: User object
            platform: Optional platform filter (DARE/SocraticBots)
            start_date: Optional start date filter
            end_date: Optional end date filter
            filename: Optional custom filename

        Returns:
            HttpResponse with CSV content
        """
        queryset = Transaction.objects.filter(user=user)

        # Apply filters
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
    Service class for wallet operations.
    """

    @staticmethod
    def add_topup(user, amount=Decimal("5.00"), message="Monthly $5 top-up"):
        """
        Add a credit transaction to the user's wallet.
        """
        with transaction.atomic():
            return Transaction.objects.create(
                user=user,
                amount=amount,
                type=TransactionTypeChoice.CREDIT,
                message=message
            )

    @staticmethod
    def has_recent_topup(user, days=30):
        """
        Check if user has received a top-up in the last N days.
        """
        cutoff_date = timezone.now() - timedelta(days=days)
        return Transaction.objects.filter(
            user=user,
            type=TransactionTypeChoice.CREDIT,
            message="Monthly $5 top-up",
            created_at__gte=cutoff_date
        ).exists()

    @staticmethod
    def is_eligible_for_topup(user):
        """
        Check if user is eligible for a top-up.
        Criteria:
        - User must be active
        - Wallet must be at least 30 days old
        - No top-up in the last 30 days
        """
        if not user.is_active:
            return False, "User is not active"

        try:
            wallet = user.wallet
        except Wallet.DoesNotExist:
            return False, "User has no wallet"

        wallet_age = timezone.now() - wallet.created_at
        if wallet_age < timedelta(days=30):
            return False, "Wallet is less than 30 days old"

        if WalletService.has_recent_topup(user):
            return False, "User already received a top-up in the last 30 days"

        return True, "User is eligible for top-up"

    @staticmethod
    def get_last_topup_date(user):
        """
        Get the date of the user's last top-up.
        """
        last_topup = Transaction.objects.filter(
            user=user,
            type=TransactionTypeChoice.CREDIT,
            message="Monthly $5 top-up"
        ).order_by('-created_at').first()

        return last_topup.created_at if last_topup else None

    @staticmethod
    def get_next_topup_date(user):
        """
        Get the date when the user will be eligible for the next top-up.
        """
        last_topup_date = WalletService.get_last_topup_date(user)

        if last_topup_date is None:
            wallet_creation_date = user.wallet.created_at
            return wallet_creation_date + timedelta(days=30)

        return last_topup_date + timedelta(days=30)

    @staticmethod
    def debit_wallet(user, amount, message="", llm=None, input_tokens=0, output_tokens=0):
        """
        Debit amount from user's wallet.
        """
        with transaction.atomic():
            return Transaction.objects.create(
                user=user,
                amount=amount,
                type=TransactionTypeChoice.DEBIT,
                message=message,
                llm=llm,
                input_tokens=input_tokens,
                output_tokens=output_tokens
            )
