import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.db import transaction
from django.db.models import Count, Prefetch, Q, QuerySet
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from api_keys.constants import BillingModeChoice
from billing.caps import SpendLimit
from billing.constants import (
    LITELLM_SPEND_LIMIT_REACHED,
    LiteLLMKeySourceChoice,
    PolicySourceChoice,
    TransactionSourceChoice,
    TransactionTypeChoice,
    UserWalletPreferenceTypeChoice,
)
from billing.exceptions import PaymentRequiredError
from billing.models import (
    GroupWallet,
    LiteLLMSpend,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    Wallet,
)
from users.models import User

if TYPE_CHECKING:
    from billing.wallet_router import ResolvedWallet

# Lets get_litellm_spend_limit read a page of members without a query each.
MEMBER_SPEND_PREFETCH = Prefetch(
    "litellm_spend", queryset=LiteLLMSpend.objects.select_related("litellm_key")
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
    cap: Decimal
    cap_source: str


class TransactionExportService:
    """
    Service class for exporting transactions to CSV format.
    """

    @staticmethod
    def export_to_csv(queryset, *, include_owner=False, filename=None):
        """
        Export a queryset of transactions as a CSV attachment.

        ``include_owner`` adds the user's email and related group, for exports
        that span several users.
        """
        timestamp = timezone.now().strftime('%Y-%m-%d')
        if filename is None:
            filename = f'transaction-history-{timestamp}.csv'

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)

        owner_headers = ['User Email', 'Related Group'] if include_owner else []
        writer.writerow(owner_headers + [
            'Charged (USD)',
            'Estimated Cost (USD)',
            'Type',
            'Source',
            'Message',
            'LLM',
            'Input Tokens',
            'Output Tokens',
            'Cached Input Tokens',
            'Billing Mode',
            'Platform',
            'Date (UTC)',
        ])

        optimized_queryset = queryset.select_related('user', 'llm', 'related_group')

        # Raw decimals, not display_amount: sub-cent costs round to $0.00 there.
        # An estimate exists only for externally billed calls, which charge 0.
        for txn in optimized_queryset.iterator(chunk_size=2000):
            owner = (
                [
                    txn.user.email,
                    txn.related_group.access_code if txn.related_group else 'N/A',
                ]
                if include_owner
                else []
            )
            writer.writerow(owner + [
                txn.amount,
                '' if txn.reference_amount is None else txn.reference_amount,
                txn.get_type_display(),
                txn.get_source_display(),
                txn.message or '',
                txn.llm_name or 'N/A',
                txn.input_tokens if txn.input_tokens is not None else 'N/A',
                txn.output_tokens if txn.output_tokens is not None else 'N/A',
                txn.cached_input_tokens if txn.cached_input_tokens is not None else 'N/A',
                txn.get_billing_mode_display(),
                txn.get_platform_display(),
                # ISO text: spreadsheets parse the spaced form as a date and
                # render it as ### in a default-width column.
                txn.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
            ])

        return response


@dataclass(frozen=True)
class TransactionHistoryQuery:
    """Filters for one user's transaction history. ``None`` means unfiltered."""

    platform: Optional[str]
    billing_mode: Optional[str]
    model: Optional[str]
    created_after: Optional[datetime]
    created_before: Optional[datetime]


class TransactionHistoryService:
    """Reads a user's transaction history for the list view and its CSV export."""

    @staticmethod
    def transactions(user, query: TransactionHistoryQuery) -> QuerySet[Transaction]:
        queryset = TransactionHistoryService._matching(user, query)
        if query.billing_mode:
            queryset = queryset.filter(billing_mode=query.billing_mode)
        # pk breaks created_at ties so page boundaries are stable.
        return queryset.select_related("llm", "related_group").order_by(
            "-created_at", "-pk"
        )

    @staticmethod
    def summary(user, query: TransactionHistoryQuery) -> dict[str, int]:
        """Counts per billing mode, so every tab badge reflects the other filters."""
        return TransactionHistoryService._matching(user, query).aggregate(
            all=Count("pk"),
            **{
                mode: Count("pk", filter=Q(billing_mode=mode))
                for mode in BillingModeChoice.values
            },
        )

    @staticmethod
    def models(user, query: TransactionHistoryQuery) -> list[str]:
        """Model names on the platform, independent of the other filters."""
        names = (
            TransactionHistoryService._on_platform(user, query.platform)
            .exclude(llm_name__isnull=True)
            .values_list("llm_name", flat=True)
            .distinct()
        )
        return sorted(names, key=str.casefold)

    @staticmethod
    def _on_platform(user, platform: Optional[str]) -> QuerySet[Transaction]:
        queryset = Transaction.objects.filter(user=user)
        return queryset if platform is None else queryset.filter(platform=platform)

    @staticmethod
    def _matching(user, query: TransactionHistoryQuery) -> QuerySet[Transaction]:
        """Every filter except billing mode, which the summary breaks down."""
        queryset = TransactionHistoryService._on_platform(user, query.platform)
        if query.model:
            queryset = queryset.filter(llm_name=query.model)
        if query.created_after:
            queryset = queryset.filter(created_at__gte=query.created_after)
        if query.created_before:
            queryset = queryset.filter(created_at__lt=query.created_before)
        return queryset


class WalletService:
    """
    Service class for wallet operations and refill-policy resolution.
    """

    # --- Refill policy resolution -----------------------------------------

    @staticmethod
    def get_effective_refill_policy(user) -> EffectiveRefillPolicy:
        """
        Resolve the user's effective refill amount, period and cap using the
        3-tier hierarchy with field-level fallthrough:

            user.refill_override.<field> -> group.group_wallet.<field> -> system

        With no cap set at any tier, the cap is the refill amount: a refill
        tops the wallet up to that amount instead of stacking on top of it.
        """
        override = WalletService._override_for(user)
        group_wallet = WalletService.get_group_wallet_for_user(user)
        tiers = (
            (PolicySourceChoice.USER, override),
            (PolicySourceChoice.GROUP, group_wallet),
            (PolicySourceChoice.SYSTEM, SystemRefillPolicy.load()),
        )

        def resolve(field):
            for source, tier in tiers:
                value = getattr(tier, field, None) if tier is not None else None
                if value is not None:
                    return value, source
            return None, PolicySourceChoice.SYSTEM

        amount, amount_source = resolve("refill_amount")
        period, period_source = resolve("refill_period_days")
        cap, cap_source = resolve("refill_cap")
        if cap is None:
            cap, cap_source = amount, amount_source
        return EffectiveRefillPolicy(
            amount=amount,
            period_days=period,
            amount_source=amount_source,
            period_source=period_source,
            cap=cap,
            cap_source=cap_source,
        )

    @staticmethod
    def get_litellm_spend_limit(user) -> Optional[SpendLimit]:
        """The member's spend limit on their group's LiteLLM keys, or None.

        The limit is the user override, else the group's per-member limit.
        Spend is summed across every key the group has been issued, so
        rotating the key does not hand members a fresh allowance.
        """
        group = getattr(user, "access_code_group", None)
        # The owner runs the course; the per-member limit is for members.
        if group is None or group.group_owner_id == user.pk:
            return None
        group_id = group.pk

        override = WalletService._override_for(user)
        group_wallet = WalletService.get_group_wallet_for_user(user)
        if override is not None and override.litellm_cap is not None:
            limit, source = override.litellm_cap, PolicySourceChoice.USER
        elif group_wallet is not None and group_wallet.litellm_member_cap is not None:
            limit, source = group_wallet.litellm_member_cap, PolicySourceChoice.GROUP
        else:
            return None

        # .all() reuses a `litellm_spend__litellm_key` prefetch when the caller made one.
        used = sum(
            (
                spend.total_reference_amount
                for spend in user.litellm_spend.all()
                if spend.litellm_key.source == LiteLLMKeySourceChoice.ADMIN_GROUP
                and spend.litellm_key.source_group_id == group_id
            ),
            Decimal("0"),
        )
        return SpendLimit(limit=limit, used=used, source=source)

    @staticmethod
    def assert_dispatch_allowed(user, wallet: "ResolvedWallet") -> None:
        """Refuse a group-key dispatch once the member has used their limit.

        Only a group-issued key carries a group, and the limit applies to that
        group's members; a user's own key, and an owner's use of their own
        group's key, are theirs to spend.
        """
        if wallet.type != UserWalletPreferenceTypeChoice.LITELLM or wallet.group is None:
            return
        # Reloaded rather than read off `user`: a chat socket holds its user for
        # the whole session, and a limit the instructor changes must apply now.
        member = (
            User.objects.select_related(
                "refill_override", "access_code_group__group_wallet"
            )
            .prefetch_related(MEMBER_SPEND_PREFETCH)
            .get(pk=user.pk)
        )
        if wallet.group.pk != member.access_code_group_id:
            return
        spend_limit = WalletService.get_litellm_spend_limit(member)
        if spend_limit is None or not spend_limit.is_reached:
            return
        raise PaymentRequiredError(
            _(
                "You have used your ${limit} allowance on your group's AI gateway. "
                "Ask your instructor to raise your limit."
            ).format(limit=f"{spend_limit.limit:.2f}"),
            code=LITELLM_SPEND_LIMIT_REACHED,
            details={"limit": str(spend_limit.limit), "used": str(spend_limit.used)},
        )

    @staticmethod
    def _override_for(user) -> Optional[UserRefillOverride]:
        try:
            return user.refill_override
        except UserRefillOverride.DoesNotExist:
            return None

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
    def debit_wallet(
        user,
        amount,
        message="",
        llm=None,
        input_tokens=0,
        output_tokens=0,
        billing_mode=None,
        related_group=None,
    ):
        """
        Record a usage transaction for ``user``.

        For ``billing_mode == WALLET`` (default) this debits the user's DARE
        wallet — existing behaviour preserved.

        For ``billing_mode == OWN_API`` (BYO) or ``LITELLM`` the transaction is
        recorded for analytics but the DARE wallet balance is not touched
        (external billing). The mode is propagated through ``Transaction.save``
        which skips the wallet mutation in those cases.
        """
        kwargs = dict(
            user=user,
            amount=amount,
            type=TransactionTypeChoice.DEBIT,
            message=message,
            llm=llm,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            source=TransactionSourceChoice.USAGE,
        )
        if billing_mode is not None:
            kwargs["billing_mode"] = billing_mode
        if related_group is not None:
            kwargs["related_group"] = related_group

        with transaction.atomic():
            return Transaction.objects.create(**kwargs)
