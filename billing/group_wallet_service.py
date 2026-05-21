"""
Group Wallet Service

Pool-funded refills and one-off allocations for group owners (professors / lab leads).
All multi-parameter entry points take frozen dataclass DTOs and run in atomic blocks.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction as db_transaction
from django.db.models import QuerySet

from billing.constants import TransactionSourceChoice, TransactionTypeChoice
from billing.models import GroupWallet, Transaction, UserRefillOverride
from users.constants import RoleChoice
from users.models import AccessCodeGroup, User


# --- DTOs -----------------------------------------------------------------

@dataclass(frozen=True)
class FundGroupBudgetRequest:
    group_wallet_id: int
    actor: Any
    amount: Decimal
    note: str = ""


@dataclass(frozen=True)
class AllocateToMemberRequest:
    group_wallet_id: int
    owner: Any
    recipient_user_id: int
    amount: Decimal
    note: str = ""


@dataclass(frozen=True)
class UpdateGroupPolicyRequest:
    group_wallet_id: int
    owner: Any
    refill_amount: Optional[Decimal] = None
    refill_period_days: Optional[int] = None
    is_active: Optional[bool] = None
    clear_amount: bool = False
    clear_period: bool = False


@dataclass(frozen=True)
class UpsertUserOverrideRequest:
    owner_or_admin: Any
    target_user_id: int
    refill_amount: Optional[Decimal] = None
    refill_period_days: Optional[int] = None
    reason: str = ""
    clear_amount: bool = False
    clear_period: bool = False


# --- Service --------------------------------------------------------------

class GroupWalletService:
    """All group-wallet write operations live here and run atomically."""

    # --- Permission helpers ------------------------------------------------

    @staticmethod
    def is_admin(user) -> bool:
        if user is None or not user.is_authenticated:
            return False
        return bool(user.is_superuser) or user.platform_role == RoleChoice.SUPERADMIN

    @staticmethod
    def assert_owner_or_admin(group_wallet: GroupWallet, user) -> None:
        if GroupWalletService.is_admin(user):
            return
        if group_wallet.group.group_owner_id == user.id:
            return
        raise PermissionDenied("You do not own this group.")

    @staticmethod
    def assert_user_in_group(target_user: User, group: AccessCodeGroup) -> None:
        if target_user.access_code_group_id != group.id:
            raise ValidationError({"recipient_user_id": "Recipient is not a member of this group."})

    # --- Reads -------------------------------------------------------------

    @staticmethod
    def list_owned_groups(owner) -> "QuerySet[AccessCodeGroup]":
        return (
            AccessCodeGroup.objects.filter(group_owner=owner, is_active=True)
            .select_related("group_wallet")
            .prefetch_related("users", "users__wallet", "users__refill_override")
        )

    # --- Writes ------------------------------------------------------------

    @staticmethod
    def fund_group_budget(req: FundGroupBudgetRequest) -> GroupWallet:
        """
        Admin-only. Add credit to a group's budget_balance without charging any
        user wallet. Records an audit Transaction on the actor with amount=0.
        """
        if not GroupWalletService.is_admin(req.actor):
            raise PermissionDenied("Only admins can fund group budgets.")
        if req.amount is None or req.amount <= 0:
            raise ValidationError({"amount": "Amount must be positive."})

        with db_transaction.atomic():
            group_wallet = GroupWallet.objects.select_for_update().get(pk=req.group_wallet_id)
            group_wallet.budget_balance = (group_wallet.budget_balance or Decimal("0")) + req.amount
            group_wallet.save(update_fields=["budget_balance", "updated_at"])

            Transaction.objects.create(
                user=req.actor,
                amount=Decimal("0"),
                type=TransactionTypeChoice.CREDIT,
                source=TransactionSourceChoice.GROUP_BUDGET_TOPUP,
                related_group=group_wallet.group,
                message=(
                    f"Admin funded group {group_wallet.group.access_code} budget by ${req.amount}"
                    + (f" — {req.note}" if req.note else "")
                ),
            )
            return group_wallet

    @staticmethod
    def allocate_to_member(req: AllocateToMemberRequest) -> Tuple[Transaction, Transaction]:
        """
        Group-owner (or admin) one-off allocation from the group pool to a member.
        Returns (owner_informational_row, member_credit_row).
        """
        if req.amount is None or req.amount <= 0:
            raise ValidationError({"amount": "Amount must be positive."})

        with db_transaction.atomic():
            group_wallet = GroupWallet.objects.select_for_update().select_related("group").get(
                pk=req.group_wallet_id
            )
            GroupWalletService.assert_owner_or_admin(group_wallet, req.owner)

            if not group_wallet.is_active:
                raise ValidationError({"group_wallet": "Group wallet is inactive."})
            if group_wallet.budget_balance < req.amount:
                raise ValidationError({
                    "amount": "Insufficient group budget.",
                    "budget_balance": str(group_wallet.budget_balance),
                    "required_amount": str(req.amount),
                })

            recipient = User.objects.get(pk=req.recipient_user_id)
            GroupWalletService.assert_user_in_group(recipient, group_wallet.group)

            group_wallet.budget_balance -= req.amount
            group_wallet.save(update_fields=["budget_balance", "updated_at"])

            owner_row = Transaction.objects.create(
                user=req.owner,
                amount=Decimal("0"),
                type=TransactionTypeChoice.DEBIT,
                source=TransactionSourceChoice.GROUP_ALLOCATION,
                related_group=group_wallet.group,
                message=(
                    f"Allocated ${req.amount} to {recipient.email}"
                    + (f" — {req.note}" if req.note else "")
                ),
            )
            member_row = Transaction.objects.create(
                user=recipient,
                amount=req.amount,
                type=TransactionTypeChoice.CREDIT,
                source=TransactionSourceChoice.GROUP_ALLOCATION,
                related_group=group_wallet.group,
                related_transaction=owner_row,
                message=(
                    f"Allocation from group {group_wallet.group.access_code}"
                    + (f" — {req.note}" if req.note else "")
                ),
            )
            owner_row.related_transaction = member_row
            owner_row.save(update_fields=["related_transaction", "updated_at"])
            return owner_row, member_row

    @staticmethod
    def update_group_policy(req: UpdateGroupPolicyRequest) -> GroupWallet:
        """
        Owner/admin updates the group's refill amount/period/active flag.
        Pass clear_amount or clear_period to unset (fall back to system default).
        """
        with db_transaction.atomic():
            group_wallet = GroupWallet.objects.select_for_update().select_related("group").get(
                pk=req.group_wallet_id
            )
            GroupWalletService.assert_owner_or_admin(group_wallet, req.owner)

            update_fields = []

            if req.clear_amount:
                group_wallet.refill_amount = None
                update_fields.append("refill_amount")
            elif req.refill_amount is not None:
                if req.refill_amount < 0:
                    raise ValidationError({"refill_amount": "Refill amount cannot be negative."})
                group_wallet.refill_amount = req.refill_amount
                update_fields.append("refill_amount")

            if req.clear_period:
                group_wallet.refill_period_days = None
                update_fields.append("refill_period_days")
            elif req.refill_period_days is not None:
                if req.refill_period_days < 1:
                    raise ValidationError({"refill_period_days": "Refill period must be at least 1 day."})
                group_wallet.refill_period_days = req.refill_period_days
                update_fields.append("refill_period_days")

            if req.is_active is not None:
                group_wallet.is_active = req.is_active
                update_fields.append("is_active")

            if update_fields:
                update_fields.append("updated_at")
                group_wallet.save(update_fields=update_fields)
            return group_wallet

    @staticmethod
    def upsert_user_override(
        req: UpsertUserOverrideRequest,
    ) -> Optional[UserRefillOverride]:
        """
        Create or update a user's refill override.
        - Group owners can only target members of their own group.
        - Admins can target any user.
        Returns None when the resulting override has no overrides set (i.e. it
        was deleted because every field fell back to inherit).
        """
        actor = req.owner_or_admin
        is_admin = GroupWalletService.is_admin(actor)

        if req.refill_amount is not None and req.refill_amount < 0:
            raise ValidationError({"refill_amount": "Refill amount cannot be negative."})
        if req.refill_period_days is not None and req.refill_period_days < 1:
            raise ValidationError({"refill_period_days": "Refill period must be at least 1 day."})

        with db_transaction.atomic():
            target_user = User.objects.select_related("access_code_group").get(pk=req.target_user_id)

            if not is_admin:
                target_group = target_user.access_code_group
                if target_group is None or target_group.group_owner_id != actor.id:
                    raise PermissionDenied("You can only override members of your own group.")

            override, _created = UserRefillOverride.objects.get_or_create(user=target_user)

            update_fields = ["updated_at", "set_by"]

            if req.clear_amount:
                override.refill_amount = None
                update_fields.append("refill_amount")
            elif req.refill_amount is not None:
                override.refill_amount = req.refill_amount
                update_fields.append("refill_amount")

            if req.clear_period:
                override.refill_period_days = None
                update_fields.append("refill_period_days")
            elif req.refill_period_days is not None:
                override.refill_period_days = req.refill_period_days
                update_fields.append("refill_period_days")

            if req.reason:
                override.reason = req.reason
                update_fields.append("reason")

            override.set_by = actor
            override.save(update_fields=list(dict.fromkeys(update_fields)))

            if (
                override.refill_amount is None
                and override.refill_period_days is None
                and not override.reason
            ):
                override.delete()
                return None
            return override

    @staticmethod
    def remove_user_override(actor, target_user_id: int) -> None:
        """Delete a user's override. Owners limited to their own group's members."""
        is_admin = GroupWalletService.is_admin(actor)
        target_user = User.objects.select_related("access_code_group").get(pk=target_user_id)
        if not is_admin:
            target_group = target_user.access_code_group
            if target_group is None or target_group.group_owner_id != actor.id:
                raise PermissionDenied("You can only clear overrides for members of your own group.")
        UserRefillOverride.objects.filter(user=target_user).delete()
