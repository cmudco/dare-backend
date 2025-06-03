from decimal import Decimal
from django_rq import job
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import Wallet, Transaction
from .constants import TransactionTypeChoice

User = get_user_model()

@job
def process_monthly_topup():
    """
    Process monthly $5 top-up for all eligible users.
    Users are eligible if:
    1. They are active
    2. Their wallet is at least 30 days old
    3. They haven't received a top-up in the last 30 days
    """
    cutoff_date = timezone.now() - timedelta(days=30)

    stats = {
        "processed": 0,
        "failed": 0,
        "total_eligible": 0,
        "ineligible_inactive_users": 0,
        "ineligible_wallet_too_new": 0,
        "ineligible_recent_topup": 0,
        "ineligible_no_wallet": 0,
        "total_users_checked": 0
    }

    all_users = User.objects.all()
    stats["total_users_checked"] = all_users.count()

    for user in all_users:
        if not user.is_active:
            stats["ineligible_inactive_users"] += 1
            continue

        try:
            wallet = user.wallet
        except:
            stats["ineligible_no_wallet"] += 1
            continue

        wallet_age = timezone.now() - wallet.created_at
        if wallet_age < timedelta(days=30):
            stats["ineligible_wallet_too_new"] += 1
            continue

        has_recent_topup = Transaction.objects.filter(
            user=user,
            type=TransactionTypeChoice.CREDIT,
            message="Monthly $5 top-up",
            created_at__gte=cutoff_date
        ).exists()

        if has_recent_topup:
            stats["ineligible_recent_topup"] += 1
            continue

        try:
            with transaction.atomic():
                Transaction.objects.create(
                    user=user,
                    amount=Decimal("5.00"),
                    type=TransactionTypeChoice.CREDIT,
                    message="Monthly $5 top-up"
                )
                stats["processed"] += 1
        except Exception as e:
            stats["failed"] += 1
            continue

    stats["total_eligible"] = stats["processed"] + stats["failed"]
    return stats

@job
def process_user_topup(user_id):
    """
    Process a $5 top-up for a specific user.
    """
    try:
        user = User.objects.get(id=user_id, is_active=True)

        cutoff_date = timezone.now() - timedelta(days=30)
        has_recent_topup = Transaction.objects.filter(
            user=user,
            type=TransactionTypeChoice.CREDIT,
            message="Monthly $5 top-up",
            created_at__gte=cutoff_date
        ).exists()

        if has_recent_topup:
            return f"User {user.email} already received a top-up in the last 30 days"

        wallet_age = timezone.now() - user.wallet.created_at
        if wallet_age < timedelta(days=30):
            return f"User {user.email} wallet is less than 30 days old"

        with transaction.atomic():
            Transaction.objects.create(
                user=user,
                amount=Decimal("5.00"),
                type=TransactionTypeChoice.CREDIT,
                message="Monthly $5 top-up"
            )

        return f"Top-up successful for user {user.email}"

    except User.DoesNotExist:
        return "User not found or inactive"
    except Exception as e:
        return f"Top-up failed: {str(e)}"
