import logging
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.utils import timezone
from django_rq import job

from billing.constants import TransactionSourceChoice, TransactionTypeChoice
from billing.models import GroupWallet, Transaction, Wallet
from billing.services import WalletService

logger = logging.getLogger(__name__)

User = get_user_model()


@job
def process_scheduled_refills():
    """
    Run through every user and, if their effective refill period has elapsed,
    credit them their effective refill amount. Group members are funded from
    the group's budget; users without a group are funded by the platform.

    Runs daily — per-user periods determine actual refill cadence.
    """
    stats = {
        "refilled": 0,
        "failed": 0,
        "not_due": 0,
        "inactive_user": 0,
        "no_wallet": 0,
        "group_inactive": 0,
        "budget_exhausted": 0,
        "total_users_checked": 0,
    }

    now = timezone.now()
    users = User.objects.all().select_related("wallet", "access_code_group")
    stats["total_users_checked"] = users.count()

    for user in users:
        try:
            _topup_single_user(user, now, stats)
        except Exception as exc:  # noqa: BLE001 — keep loop going; record failure
            stats["failed"] += 1
            logger.exception("Scheduled refill failed for user %s: %s", getattr(user, "email", user.pk), exc)

    return stats


@job
def process_user_topup(user_id):
    """Single-user manual refill, respecting the same eligibility + funding rules."""
    try:
        user = User.objects.select_related("wallet", "access_code_group").get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return "User not found or inactive"

    stats = {"refilled": 0, "failed": 0, "not_due": 0, "inactive_user": 0,
             "no_wallet": 0, "group_inactive": 0, "budget_exhausted": 0, "total_users_checked": 1}
    _topup_single_user(user, timezone.now(), stats)
    if stats["refilled"]:
        return f"Top-up successful for {user.email}"
    if stats["not_due"]:
        return f"{user.email} is not yet due for a refill"
    if stats["budget_exhausted"]:
        return f"Group budget exhausted for {user.email}"
    if stats["group_inactive"]:
        return f"Group inactive for {user.email}"
    if stats["no_wallet"]:
        return f"{user.email} has no wallet"
    return f"No refill for {user.email}: {stats}"


def _topup_single_user(user, now, stats):
    """Process a single user against the 3-tier policy and fund accordingly."""
    if not user.is_active:
        stats["inactive_user"] += 1
        return

    try:
        wallet = user.wallet
    except Wallet.DoesNotExist:
        stats["no_wallet"] += 1
        return

    if not WalletService.is_user_due_for_refill(user, now=now):
        stats["not_due"] += 1
        return

    policy = WalletService.get_effective_refill_policy(user)
    if policy.amount <= 0:
        stats["not_due"] += 1
        return

    group_wallet = WalletService.get_group_wallet_for_user(user)

    if group_wallet is not None:
        if not group_wallet.is_active:
            stats["group_inactive"] += 1
            return
        _refill_from_group_budget(user, wallet, group_wallet, policy, now, stats)
    else:
        _refill_from_system(user, wallet, policy, now, stats)


def _refill_from_group_budget(user, wallet, group_wallet, policy, now, stats):
    """Debit the group budget and credit the member in a single atomic block.

    When the group has an owner, write an informational zero-amount DEBIT row
    on the owner so it shows up in their transaction history; otherwise skip
    the owner row entirely (no self-referential rows on the member).
    """
    with db_transaction.atomic():
        gw = GroupWallet.objects.select_for_update().select_related("group", "group__group_owner").get(pk=group_wallet.pk)
        if gw.budget_balance < policy.amount:
            stats["budget_exhausted"] += 1
            return
        gw.budget_balance -= policy.amount
        gw.save(update_fields=["budget_balance", "updated_at"])

        owner_row = None
        if gw.group.group_owner_id is not None:
            owner_row = Transaction.objects.create(
                user=gw.group.group_owner,
                amount=Decimal("0"),
                type=TransactionTypeChoice.DEBIT,
                source=TransactionSourceChoice.SCHEDULED_REFILL,
                related_group=gw.group,
                message=f"Scheduled refill: ${policy.amount} from group budget to {user.email}",
            )

        member_row = Transaction.objects.create(
            user=user,
            amount=policy.amount,
            type=TransactionTypeChoice.CREDIT,
            source=TransactionSourceChoice.SCHEDULED_REFILL,
            related_group=gw.group,
            related_transaction=owner_row,
            message=f"Scheduled refill from group {gw.group.access_code}",
        )
        if owner_row is not None:
            owner_row.related_transaction = member_row
            owner_row.save(update_fields=["related_transaction", "updated_at"])

        wallet.last_refill_at = now
        wallet.save(update_fields=["last_refill_at", "updated_at"])
        stats["refilled"] += 1


def _refill_from_system(user, wallet, policy, now, stats):
    """Platform-funded refill (user has no group). Credits wallet directly."""
    with db_transaction.atomic():
        Transaction.objects.create(
            user=user,
            amount=policy.amount,
            type=TransactionTypeChoice.CREDIT,
            source=TransactionSourceChoice.SCHEDULED_REFILL,
            message=f"Scheduled refill: ${policy.amount}",
        )
        wallet.last_refill_at = now
        wallet.save(update_fields=["last_refill_at", "updated_at"])
        stats["refilled"] += 1


# --- Backwards-compat alias ------------------------------------------------
# The pre-existing scheduler references `process_monthly_topup`. Keep a thin
# alias so queued jobs keep working during the transition.
@job
def process_monthly_topup():
    return process_scheduled_refills()
