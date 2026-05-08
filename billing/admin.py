import json
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from agents.models import Agent
from billing.constants import (LiteLLMKeySourceChoice, TransactionSourceChoice,
                               TransactionTypeChoice)
from billing.group_wallet_service import (FundGroupBudgetRequest,
                                          GroupWalletService,
                                          UpdateGroupPolicyRequest)
from billing.models import (GroupWallet, LiteLLMKey,
                            SystemRefillPolicy, Transaction,
                            UserRefillOverride, UserWalletPreference, Wallet)
from billing.services import TransactionExportService
from conversations.constants import ModelTier, Provider
from conversations.models import Message
from files.models import File
from users.models import User
from workflows.models.core import WorkflowRun


class TokenUsageFilter(admin.SimpleListFilter):
    """Filter transactions by token usage ranges."""

    title = "token usage"
    parameter_name = "token_usage"

    def lookups(self, request, model_admin):
        return (
            ("low", "Low (0-1K tokens)"),
            ("medium", "Medium (1K-10K tokens)"),
            ("high", "High (10K-50K tokens)"),
            ("very_high", "Very High (50K+ tokens)"),
            ("zero", "Zero tokens"),
        )

    def queryset(self, request, queryset):
        if self.value() == "zero":
            return queryset.filter(
                Q(input_tokens=0, output_tokens=0)
                | Q(input_tokens__isnull=True)
                | Q(output_tokens__isnull=True)
            )
        elif self.value() == "low":
            return queryset.filter(
                Q(input_tokens__isnull=False)
                & Q(output_tokens__isnull=False)
                & Q(input_tokens__gte=0, input_tokens__lt=1000)
                & Q(output_tokens__gte=0, output_tokens__lt=1000)
            ).exclude(Q(input_tokens=0) & Q(output_tokens=0))
        elif self.value() == "medium":
            return queryset.filter(
                Q(input_tokens__isnull=False) & Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=1000, input_tokens__lt=10000)
                | Q(output_tokens__gte=1000, output_tokens__lt=10000)
            )
        elif self.value() == "high":
            return queryset.filter(
                Q(input_tokens__isnull=False) & Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=10000, input_tokens__lt=50000)
                | Q(output_tokens__gte=10000, output_tokens__lt=50000)
            )
        elif self.value() == "very_high":
            return queryset.filter(
                Q(input_tokens__isnull=False) & Q(output_tokens__isnull=False)
            ).filter(Q(input_tokens__gte=50000) | Q(output_tokens__gte=50000))
        return queryset


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "display_balance",
        "last_refill_at",
        "created_at",
        "updated_at",
    )
    search_fields = ("user__email",)
    list_filter = ("user__is_active",)
    ordering = ("-balance",)
    readonly_fields = ("balance", "last_refill_at", "created_at", "updated_at")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = db_field.related_model.objects.order_by("email")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "display_amount",
        "type",
        "source",
        "related_group",
        "platform",
        "billing_mode",
        "llm_name",
        "input_tokens",
        "output_tokens",
        "total_tokens_display",
        "message",
        "created_at",
    )
    list_filter = (
        "type",
        "source",
        "platform",
        "billing_mode",
        "created_at",
        "llm_name",
        TokenUsageFilter,
    )
    search_fields = ("user__email", "message", "llm_name", "related_group__access_code")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = (
        "display_amount",
        "llm_name",
        "input_tokens",
        "output_tokens",
        "total_tokens_display",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("related_group", "related_transaction")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = db_field.related_model.objects.order_by("email")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def display_amount(self, obj):
        return obj.display_amount if obj else "N/A"

    display_amount.short_description = "Amount"
    actions = ["export_transactions_to_csv"]

    fieldsets = (
        (
            "Transaction Info",
            {
                "fields": (
                    "user",
                    "type",
                    "source",
                    "platform",
                    "billing_mode",
                    "message",
                )
            },
        ),
        (
            "Group Linkage",
            {
                "fields": ("related_group", "related_transaction"),
                "classes": ("collapse",),
                "description": "Links to the AccessCodeGroup this transaction belongs to and any paired transaction.",
            },
        ),
        (
            "Billing Details",
            {"fields": ("amount", "display_amount", "llm", "llm_name")},
        ),
        (
            "Token Usage",
            {
                "fields": ("input_tokens", "output_tokens", "total_tokens_display"),
                "description": "Token consumption metrics for this transaction",
            },
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def total_tokens_display(self, obj):
        if obj.input_tokens is not None and obj.output_tokens is not None:
            total = obj.input_tokens + obj.output_tokens
            return f"{total:,}"
        return "N/A"

    total_tokens_display.short_description = "Total Tokens"
    total_tokens_display.admin_order_field = "input_tokens"

    def export_transactions_to_csv(self, request, queryset):
        return TransactionExportService.export_to_csv(queryset)

    export_transactions_to_csv.short_description = "Export selected transactions to CSV"


# --- System refill policy (singleton) -------------------------------------


@admin.register(SystemRefillPolicy)
class SystemRefillPolicyAdmin(admin.ModelAdmin):
    list_display = ("refill_amount", "refill_period_days", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    def has_add_permission(self, request):
        # Singleton — prevent additional rows
        return not SystemRefillPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# --- Group wallet ---------------------------------------------------------


class GroupWalletActionForm(ActionForm):
    amount = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        max_digits=15,
        decimal_places=6,
        help_text="Amount (USD). Required for 'fund' action; optional for 'set policy'.",
    )
    period_days = forms.IntegerField(
        required=False, min_value=1, help_text="Refill period in days. Optional."
    )
    note = forms.CharField(required=False, max_length=255)


@admin.register(GroupWallet)
class GroupWalletAdmin(admin.ModelAdmin):
    list_display = (
        "group",
        "group_owner_display",
        "budget_balance",
        "refill_amount",
        "refill_period_days",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("group__access_code", "group__group_owner__email")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("group",)
    action_form = GroupWalletActionForm
    actions = ["fund_budget_action", "set_policy_action"]

    def group_owner_display(self, obj):
        owner = obj.group.group_owner if obj.group else None
        return owner.email if owner else "—"

    group_owner_display.short_description = "Group Owner"

    def save_model(self, request, obj, form, change):
        """Direct edits to budget_balance auto-log an audit Transaction so the
        group's funding history stays reconstructable regardless of which path
        (action vs. direct edit) the admin used."""
        old_balance = Decimal("0")
        if change and obj.pk:
            old_balance = GroupWallet.objects.filter(pk=obj.pk).values_list(
                "budget_balance", flat=True
            ).first() or Decimal("0")
        super().save_model(request, obj, form, change)
        new_balance = obj.budget_balance or Decimal("0")
        delta = new_balance - old_balance
        if delta != 0:
            sign = "+" if delta > 0 else ""
            Transaction.objects.create(
                user=request.user,
                amount=Decimal("0"),
                type=TransactionTypeChoice.CREDIT,
                source=TransactionSourceChoice.GROUP_BUDGET_TOPUP,
                related_group=obj.group,
                message=(
                    f"Admin direct-edit on {obj.group.access_code}: "
                    f"budget ${old_balance} → ${new_balance} ({sign}{delta})"
                ),
            )

    @admin.action(description="Fund selected group budget(s) by the given amount")
    def fund_budget_action(self, request, queryset):
        amount_raw = request.POST.get("amount")
        note = request.POST.get("note") or "Admin budget top-up"
        try:
            amount = Decimal(amount_raw)
        except Exception:
            self.message_user(
                request, "Please provide a valid amount.", level=messages.ERROR
            )
            return
        funded = 0
        for gw in queryset:
            try:
                GroupWalletService.fund_group_budget(
                    FundGroupBudgetRequest(
                        group_wallet_id=gw.id,
                        actor=request.user,
                        amount=amount,
                        note=note,
                    )
                )
                funded += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(
                    request,
                    f"Skipped {gw.group.access_code}: {exc}",
                    level=messages.WARNING,
                )
        self.message_user(
            request,
            f"Funded {funded} group budget(s) with ${amount}.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Set refill policy on selected group wallet(s)")
    def set_policy_action(self, request, queryset):
        amount_raw = request.POST.get("amount")
        period_raw = request.POST.get("period_days")
        amount = None
        period = None
        try:
            if amount_raw:
                amount = Decimal(amount_raw)
        except Exception:
            self.message_user(request, "Invalid amount.", level=messages.ERROR)
            return
        try:
            if period_raw:
                period = int(period_raw)
        except Exception:
            self.message_user(request, "Invalid period.", level=messages.ERROR)
            return
        if amount is None and period is None:
            self.message_user(
                request, "Provide amount, period_days, or both.", level=messages.ERROR
            )
            return

        changed = 0
        for gw in queryset:
            try:
                GroupWalletService.update_group_policy(
                    UpdateGroupPolicyRequest(
                        group_wallet_id=gw.id,
                        owner=request.user,
                        refill_amount=amount,
                        refill_period_days=period,
                    )
                )
                changed += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(
                    request,
                    f"Skipped {gw.group.access_code}: {exc}",
                    level=messages.WARNING,
                )
        self.message_user(
            request,
            f"Updated policy on {changed} group wallet(s).",
            level=messages.SUCCESS,
        )


class GroupWalletInline(admin.StackedInline):
    """Inline on AccessCodeGroupAdmin so admins can configure the wallet and set
    the initial budget at group creation time. Direct edits here are audited via
    AccessCodeGroupAdmin.save_formset."""

    model = GroupWallet
    can_delete = False
    extra = 0
    readonly_fields = ("created_at", "updated_at")
    fk_name = "group"
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "budget_balance",
                    "refill_amount",
                    "refill_period_days",
                    "is_active",
                ),
                "description": (
                    "Budget fund the group; drained by scheduled refills and one-off "
                    "allocations. Leave refill fields blank to inherit the system default."
                ),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


# --- User refill overrides ------------------------------------------------


@admin.register(UserRefillOverride)
class UserRefillOverrideAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "refill_amount",
        "refill_period_days",
        "set_by",
        "updated_at",
    )
    search_fields = ("user__email", "reason")
    raw_id_fields = ("user", "set_by")
    readonly_fields = ("created_at", "updated_at")


class UserRefillOverrideInline(admin.StackedInline):
    """Inline so admins can see/edit override directly on UserAdmin."""

    model = UserRefillOverride
    can_delete = True
    extra = 0
    fk_name = "user"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "refill_amount",
                    "refill_period_days",
                    "reason",
                    "set_by",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )




# --- LiteLLM Key admin ----------------------------------------------------


class IsExpiredFilter(admin.SimpleListFilter):
    title = _("expired")
    parameter_name = "expired"

    def lookups(self, request, model_admin):
        return (("yes", _("Expired")), ("no", _("Active")))

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "yes":
            return queryset.filter(expires_at__isnull=False, expires_at__lte=now)
        if self.value() == "no":
            return queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        return queryset


@admin.register(LiteLLMKey)
class LiteLLMKeyAdmin(admin.ModelAdmin):
    """
    Admin for LiteLLM credential rows. Superadmins create ADMIN_USER /
    ADMIN_GROUP keys here; user-self-served (USER) rows are created via
    POST /api/billing/wallets/litellm/ and are read-mostly here.

    Permission model:
      - Superadmins: full access.
      - AccessCodeGroup owners: visible-only for their own group(s); no
        create/delete from this admin (per spec: cohort key creation
        happens via the AccessCodeGroupAdmin inline so the parent group
        context is enforced).
    """

    list_display = (
        "label",
        "source",
        "owner_display",
        "expires_at",
        "is_expired_flag",
        "created_by",
        "created_at",
    )
    list_filter = ("source", IsExpiredFilter, "source_group")
    search_fields = (
        "label",
        "owner_user__email",
        "assigned_user__email",
        "source_group__access_code",
    )
    readonly_fields = ("created_at", "updated_at")
    actions = ["revoke_selected"]
    raw_id_fields = ("owner_user", "assigned_user", "source_group", "created_by")

    fieldsets = (
        (None, {"fields": ("label", "base_url", "api_key")}),
        (
            _("Source"),
            {
                "fields": (
                    "source",
                    "owner_user",
                    "assigned_user",
                    "source_group",
                    "expires_at",
                )
            },
        ),
        (
            _("Audit"),
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_module_permission(self, request):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Group owners can view rows for groups they own.
        return request.user.owned_access_code_groups.exists()

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .select_related("owner_user", "assigned_user", "source_group", "created_by")
        )
        if request.user.is_superuser:
            return qs
        return qs.filter(
            source=LiteLLMKeySourceChoice.ADMIN_GROUP,
            source_group__group_owner=request.user,
        )

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return request.user.owned_access_code_groups.exists()
        return (
            obj.source == LiteLLMKeySourceChoice.ADMIN_GROUP
            and obj.source_group
            and obj.source_group.group_owner_id == request.user.id
        )

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        return bool(request.user.is_superuser)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # Source/owner fields are immutable post-create — changing them
            # would silently re-route a credential. Force delete + recreate.
            ro += ["source", "owner_user", "assigned_user", "source_group"]
        return tuple(ro)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def owner_display(self, obj):
        if obj.source == LiteLLMKeySourceChoice.USER:
            return f"User: {obj.owner_user}" if obj.owner_user else "User: -"
        if obj.source == LiteLLMKeySourceChoice.ADMIN_USER:
            return (
                f"Assigned: {obj.assigned_user}" if obj.assigned_user else "Assigned: -"
            )
        if obj.source == LiteLLMKeySourceChoice.ADMIN_GROUP:
            return f"Group: {obj.source_group}" if obj.source_group else "Group: -"
        return ""

    owner_display.short_description = _("Owner")

    def is_expired_flag(self, obj):
        return obj.is_expired

    is_expired_flag.boolean = True
    is_expired_flag.short_description = _("Expired")

    @admin.action(description=_("Revoke selected (set expires_at = now)"))
    def revoke_selected(self, request, queryset):
        # Hard delete preserves no audit trail; setting expires_at=now does.
        # This also fires the LiteLLMKey-driven UserWalletPreference reset
        # implicitly because visible_for_user filters out past-expiry.
        updated = queryset.update(expires_at=timezone.now())
        self.message_user(
            request,
            f"Revoked {updated} key(s) by setting expires_at=now.",
            level=messages.INFO,
        )


# --- LiteLLM cohort key inline on AccessCodeGroupAdmin --------------------
#
# Wired up in users/admin.py to keep AccessCodeGroupAdmin as the single
# registration site for that model. Importing here would create a circular
# admin discovery dependency.


# --- User wallet preference ----------------------------------------------


@admin.register(UserWalletPreference)
class UserWalletPreferenceAdmin(admin.ModelAdmin):
    """
    Read-mostly view of per-user active wallet selections. Admins should
    NOT silently override a users selection — instead, revoking the
    underlying credential triggers a server-side cascade-reset.
    """

    list_display = ("user", "active_wallet_type", "active_wallet_ref_id", "updated_at")
    list_filter = ("active_wallet_type",)
    search_fields = ("user__email",)
    readonly_fields = (
        "user",
        "active_wallet_type",
        "active_wallet_ref_id",
        "created_at",
        "updated_at",
    )

    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ============================================================================
# Usage Dashboard — module-level helpers
# ============================================================================

_VALID_PERIODS = frozenset({"all", "7d", "30d", "90d"})

_PERIOD_DAYS: dict = {"7d": 7, "30d": 30, "90d": 90}

_PERIOD_LABELS: dict = {
    "all": "all time",
    "7d": "last 7 days",
    "30d": "last 30 days",
    "90d": "last 90 days",
}


def _period_cutoff(period: str) -> tuple:
    """
    Returns (cutoff, prev_cutoff) for the period.
    Both None for 'all' — callers apply no date filter.
    prev_cutoff is the start of the equivalent prior window (used for deltas).
    """
    if period == "all":
        return None, None
    days = _PERIOD_DAYS[period]
    cutoff = timezone.now() - timedelta(days=days)
    return cutoff, cutoff - timedelta(days=days)


def _pct_delta(
    current: int | float | Decimal, previous: int | float | Decimal
) -> float | None:
    """Percentage change from previous to current. None when prior is zero/None."""
    try:
        c, p = float(current), float(previous)
    except (TypeError, ValueError):
        return None
    if not p:
        return None
    return round((c - p) / p * 100, 1)


def _make_delta(delta: float | None) -> dict | None:
    """Wraps a raw delta into a template-ready dict, or None when unavailable."""
    if delta is None:
        return None
    return {
        "value": f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%",
        "up": delta >= 0,
    }


# ============================================================================
# Usage Dashboard — read-only aggregate admin view
# ============================================================================


class UsageDashboard(Transaction):
    """Proxy model used solely as an admin entry-point for the aggregate dashboard."""

    class Meta:
        proxy = True
        verbose_name = "Usage Dashboard"
        verbose_name_plural = "Usage Dashboard"


class _UsageDashboardChangeList(ChangeList):
    """
    Drops dashboard-only GET params (e.g. `period`) before Django's ChangeList
    interprets them as field lookups. Without this, clicking a period tab
    raises IncorrectLookupParameters and bounces the page to ?e=1.
    """

    _IGNORED_PARAMS = ("period",)

    def get_filters_params(self, params=None):
        filters = super().get_filters_params(params)
        for key in self._IGNORED_PARAMS:
            filters.pop(key, None)
        return filters


@admin.register(UsageDashboard)
class UsageDashboardAdmin(admin.ModelAdmin):
    """
    Read-only platform-wide usage and cost dashboard.
    All data is aggregated from Transaction, Message, and Wallet records.
    """

    change_list_template = "admin/billing/usagedashboard/change_list.html"

    def get_changelist(self, request, **kwargs):
        return _UsageDashboardChangeList

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        period = request.GET.get("period", "all")
        if period not in _VALID_PERIODS:
            period = "all"

        model_breakdown = self._get_model_breakdown(period)
        provider_split = self._get_provider_split(period)
        tier_split = self._get_tier_split(period)
        sustainability = self._get_sustainability(period)
        top_spenders = self._get_top_spenders(period)

        extra_context = extra_context or {}
        extra_context.update(
            {
                "period": period,
                "period_label": _PERIOD_LABELS[period],
                "overview": self._get_overview(period),
                "model_breakdown": model_breakdown,
                "provider_split": provider_split,
                "tier_split": tier_split,
                "sustainability": sustainability,
                "platform_split": self._get_platform_split(period),
                "billing_mode_split": self._get_billing_mode_split(period),
                "top_spenders": top_spenders,
                "churn_signals": self._get_churn_signals(),
                "orphaned_wallets": self._get_orphaned_wallets(),
                "daily_trend_json": self._get_daily_trend_json(period),
                "model_chart_json": self._build_model_chart_json(model_breakdown),
                "provider_chart_json": json.dumps(
                    [
                        {
                            "name": row["label"],
                            "cost": float(row["total_cost"] or 0),
                            "calls": row["llm_calls"],
                        }
                        for row in provider_split
                    ]
                ),
                "tier_chart_json": json.dumps(
                    [
                        {
                            "name": row["label"],
                            "cost": float(row["total_cost"] or 0),
                            "calls": row["llm_calls"],
                        }
                        for row in tier_split
                    ]
                ),
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    # ============================================================================
    # Private Helpers
    # ============================================================================

    def _get_overview(self, period: str) -> dict:
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cutoff, prev_cutoff = _period_cutoff(period)

        ai_base = Message.active_objects.filter(llm__isnull=False)
        fin_base = Transaction.objects.filter(type=TransactionTypeChoice.DEBIT)

        # Current period slices
        curr_ai = ai_base.filter(created_at__gte=cutoff) if cutoff else ai_base
        curr_fin = fin_base.filter(created_at__gte=cutoff) if cutoff else fin_base

        # Previous equivalent period slices (for deltas — empty when period == "all")
        prev_ai = (
            ai_base.filter(created_at__gte=prev_cutoff, created_at__lt=cutoff)
            if prev_cutoff
            else ai_base.none()
        )
        prev_fin = (
            fin_base.filter(created_at__gte=prev_cutoff, created_at__lt=cutoff)
            if prev_cutoff
            else fin_base.none()
        )

        # ── Current period aggregates ─────────────────────────────────────────
        curr_tok = curr_ai.aggregate(inp=Sum("input_tokens"), out=Sum("output_tokens"))
        curr_inp = curr_tok["inp"] or 0
        curr_out = curr_tok["out"] or 0
        curr_calls = curr_ai.count()
        curr_active = curr_ai.values("conversation__user").distinct().count()
        curr_spend = curr_fin.aggregate(s=Sum("amount"))["s"] or Decimal("0")

        # Zero-spend active: users with LLM calls but no wallet debit in the period.
        # Indicates what share of active users runs exclusively on free/zero-rate models.
        active_ids = set(
            curr_ai.values_list("conversation__user_id", flat=True).distinct()
        )
        paid_ids = set(
            curr_fin.filter(amount__gt=Decimal("0"))
            .values_list("user_id", flat=True)
            .distinct()
        )
        zero_spend_active = len(active_ids - paid_ids)

        # ── Previous period aggregates ────────────────────────────────────────
        prev_tok = prev_ai.aggregate(inp=Sum("input_tokens"), out=Sum("output_tokens"))
        prev_inp = prev_tok["inp"] or 0
        prev_out = prev_tok["out"] or 0
        prev_calls = prev_ai.count()
        prev_active = prev_ai.values("conversation__user").distinct().count()
        prev_spend = prev_fin.aggregate(s=Sum("amount"))["s"] or Decimal("0")

        # ── Always-current (no period filter) ────────────────────────────────
        wallet_pool = Wallet.objects.aggregate(t=Sum("balance"))["t"] or Decimal("0")
        mau = (
            ai_base.filter(created_at__gte=now - timedelta(days=30))
            .values("conversation__user")
            .distinct()
            .count()
        )
        dau = (
            ai_base.filter(created_at__gte=today_start)
            .values("conversation__user")
            .distinct()
            .count()
        )
        new_users = User.objects.filter(date_joined__gte=month_start).count()

        return {
            # Period-sensitive
            "total_spend": curr_spend,
            "total_tokens": curr_inp + curr_out,
            "total_input_tokens": curr_inp,
            "total_output_tokens": curr_out,
            "llm_calls": curr_calls,
            "active_users": curr_active,
            "zero_spend_active": zero_spend_active,
            # Period-over-period deltas (None when period == "all")
            "spend_delta": _make_delta(_pct_delta(curr_spend, prev_spend)),
            "tokens_delta": _make_delta(
                _pct_delta(curr_inp + curr_out, prev_inp + prev_out)
            ),
            "calls_delta": _make_delta(_pct_delta(curr_calls, prev_calls)),
            "active_delta": _make_delta(_pct_delta(curr_active, prev_active)),
            # Always-current
            "wallet_pool": wallet_pool,
            "mau": mau,
            "dau": dau,
            "new_users_this_month": new_users,
            "avg_spend_per_active": (
                curr_spend / curr_active if curr_active else Decimal("0")
            ),
        }

    def _get_model_breakdown(self, period: str) -> list:
        cutoff, _ = _period_cutoff(period)
        qs = Message.active_objects.filter(llm__isnull=False)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)
        rows = list(
            qs.values("llm__name")
            .annotate(
                total_cost=Sum("cost"),
                total_input=Sum("input_tokens"),
                total_output=Sum("output_tokens"),
                llm_calls=Count("id"),
                user_count=Count("conversation__user", distinct=True),
            )
            .order_by("-llm_calls")[:10]
        )
        for row in rows:
            row["llm_name"] = row.pop("llm__name") or "Unknown"
            row["total_tokens"] = (row["total_input"] or 0) + (row["total_output"] or 0)
            calls = row["llm_calls"] or 1
            row["avg_cost_per_call"] = (row["total_cost"] or Decimal("0")) / calls
        return rows

    def _get_provider_split(self, period: str) -> list:
        """Cost / calls / users grouped by `LLM.provider`. Used by the donut chart."""
        cutoff, _ = _period_cutoff(period)
        qs = Message.active_objects.filter(llm__isnull=False)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)
        rows = list(
            qs.values("llm__provider")
            .annotate(
                total_cost=Sum("cost"),
                total_input=Sum("input_tokens"),
                total_output=Sum("output_tokens"),
                llm_calls=Count("id"),
                user_count=Count("conversation__user", distinct=True),
            )
            .order_by("-total_cost")
        )
        provider_label = {p.value: p.name.title() for p in Provider}
        total = sum((r["total_cost"] or Decimal("0")) for r in rows)
        for row in rows:
            key = row.pop("llm__provider") or "unknown"
            row["provider"] = key
            row["label"] = provider_label.get(key, key.title())
            row["total_tokens"] = (row["total_input"] or 0) + (row["total_output"] or 0)
            row["share_pct"] = (
                float((row["total_cost"] or Decimal("0")) / total * 100) if total else 0
            )
        return rows

    def _get_tier_split(self, period: str) -> list:
        """Cost / calls / users grouped by `LLM.tier` (Premium/Advanced/Flash)."""
        cutoff, _ = _period_cutoff(period)
        qs = Message.active_objects.filter(llm__isnull=False)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)
        rows = list(
            qs.values("llm__tier")
            .annotate(
                total_cost=Sum("cost"),
                total_input=Sum("input_tokens"),
                total_output=Sum("output_tokens"),
                llm_calls=Count("id"),
                user_count=Count("conversation__user", distinct=True),
            )
            .order_by("-total_cost")
        )
        tier_label = dict(ModelTier.choices)
        total = sum((r["total_cost"] or Decimal("0")) for r in rows)
        for row in rows:
            key = row.pop("llm__tier") or "unknown"
            row["tier"] = key
            row["label"] = tier_label.get(key, key.title())
            row["total_tokens"] = (row["total_input"] or 0) + (row["total_output"] or 0)
            row["share_pct"] = (
                float((row["total_cost"] or Decimal("0")) / total * 100) if total else 0
            )
        return rows

    def _get_sustainability(self, period: str) -> dict:
        """
        Total environmental impact (energy, CO2, water) and a per-provider
        breakdown. All values aggregated from `Message` energy fields.
        """
        cutoff, _ = _period_cutoff(period)
        qs = Message.active_objects.filter(llm__isnull=False)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)

        totals = qs.aggregate(
            energy_wh=Sum("energy_wh"),
            carbon_g=Sum("carbon_g"),
            water_ml=Sum("water_ml"),
        )
        by_provider = list(
            qs.values("llm__provider")
            .annotate(
                energy_wh=Sum("energy_wh"),
                carbon_g=Sum("carbon_g"),
                water_ml=Sum("water_ml"),
                llm_calls=Count("id"),
            )
            .order_by("-energy_wh")
        )
        provider_label = {p.value: p.name.title() for p in Provider}
        for row in by_provider:
            key = row.pop("llm__provider") or "unknown"
            row["label"] = provider_label.get(key, key.title())
        return {
            "energy_wh": totals["energy_wh"] or Decimal("0"),
            "carbon_g": totals["carbon_g"] or Decimal("0"),
            "water_ml": totals["water_ml"] or Decimal("0"),
            "by_provider": by_provider,
        }

    def _get_platform_split(self, period: str) -> list:
        cutoff, _ = _period_cutoff(period)
        qs = Message.active_objects.filter(llm__isnull=False)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)
        return list(
            qs.values("conversation__source")
            .annotate(
                total_cost=Sum("cost"),
                llm_calls=Count("id"),
                user_count=Count("conversation__user", distinct=True),
            )
            .order_by("conversation__source")
        )

    def _get_billing_mode_split(self, period: str) -> list:
        cutoff, _ = _period_cutoff(period)
        qs = Transaction.objects.filter(type=TransactionTypeChoice.DEBIT)
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)
        return list(
            qs.values("billing_mode")
            .annotate(
                total_cost=Sum("amount"),
                transaction_count=Count("id"),
                user_count=Count("user", distinct=True),
            )
            .order_by("billing_mode")
        )

    def _get_top_spenders(self, period: str) -> list:
        """
        Top users for the period, ranked by total tokens. Each row carries
        spend + token + call totals plus activity intensity (conversations,
        files, agents, workflow runs) so admins can spot power users at a
        glance. Rows above the 90th-percentile token count are flagged
        `is_power_user=True` for badging in the template.
        """
        cutoff, _ = _period_cutoff(period)
        ai_qs = Message.active_objects.filter(llm__isnull=False)
        fin_qs = Transaction.objects.filter(type=TransactionTypeChoice.DEBIT)
        if cutoff:
            ai_qs = ai_qs.filter(created_at__gte=cutoff)
            fin_qs = fin_qs.filter(created_at__gte=cutoff)

        msg_rows = {
            row["conversation__user"]: row
            for row in ai_qs.values("conversation__user").annotate(
                total_input=Sum("input_tokens"),
                total_output=Sum("output_tokens"),
                llm_calls=Count("id"),
                conversations=Count("conversation", distinct=True),
                last_active=Max("created_at"),
            )
        }
        tx_rows = {
            row["user"]: row
            for row in fin_qs.values("user").annotate(total_spend=Sum("amount"))
        }

        user_ids = set(msg_rows) | set(tx_rows)
        user_ids.discard(None)
        user_emails = {
            u.pk: u.email
            for u in User.objects.filter(pk__in=user_ids).only("pk", "email")
        }

        # Activity intensity counts — one batched query per dimension. Files
        # and workflow-runs respect the period; agents are durable assets so
        # we use the lifetime count.
        file_qs = File.active_objects.filter(user_id__in=user_ids)
        wfrun_qs = WorkflowRun.active_objects.filter(user_id__in=user_ids)
        if cutoff:
            file_qs = file_qs.filter(created_at__gte=cutoff)
            wfrun_qs = wfrun_qs.filter(created_at__gte=cutoff)
        file_counts = dict(
            file_qs.values("user_id")
            .annotate(c=Count("id"))
            .values_list("user_id", "c")
        )
        wfrun_counts = dict(
            wfrun_qs.values("user_id")
            .annotate(c=Count("id"))
            .values_list("user_id", "c")
        )
        agent_counts = dict(
            Agent.active_objects.filter(user_id__in=user_ids)
            .values("user_id")
            .annotate(c=Count("id"))
            .values_list("user_id", "c")
        )

        merged = []
        for uid in user_ids:
            m = msg_rows.get(uid, {})
            t = tx_rows.get(uid, {})
            inp = m.get("total_input") or 0
            out = m.get("total_output") or 0
            merged.append(
                {
                    "user__email": user_emails.get(uid, ""),
                    "total_spend": t.get("total_spend") or Decimal("0"),
                    "total_tokens": inp + out,
                    "llm_calls": m.get("llm_calls") or 0,
                    "conversations": m.get("conversations") or 0,
                    "files": file_counts.get(uid, 0),
                    "workflow_runs": wfrun_counts.get(uid, 0),
                    "agents": agent_counts.get(uid, 0),
                    "last_active": m.get("last_active"),
                }
            )

        merged = [r for r in merged if r["user__email"]]
        merged.sort(key=lambda r: r["total_tokens"], reverse=True)
        merged = merged[:20]

        # Flag power users (top decile by tokens within this slice).
        if merged:
            sorted_tokens = sorted((r["total_tokens"] for r in merged), reverse=True)
            cutoff_idx = max(1, len(sorted_tokens) // 10)
            p90 = sorted_tokens[cutoff_idx - 1]
            for r in merged:
                r["is_power_user"] = r["total_tokens"] >= p90 and r["total_tokens"] > 0
        return merged

    def _get_churn_signals(self) -> list:
        """Wallets with balance > 0 but no LLM activity in the last 14 days."""
        cutoff_14d = timezone.now() - timedelta(days=14)
        recently_active_ids = set(
            Message.active_objects.filter(
                llm__isnull=False,
                created_at__gte=cutoff_14d,
            )
            .values_list("conversation__user_id", flat=True)
            .distinct()
        )
        wallets = list(
            Wallet.objects.filter(balance__gt=Decimal("0"))
            .exclude(user_id__in=recently_active_ids)
            .select_related("user")
            .order_by("-balance")[:20]
        )
        # Fetch last message date per user to avoid traversing wallet→user→conversation FK
        uid_list = [w.user_id for w in wallets]
        last_seen = dict(
            Message.active_objects.filter(
                llm__isnull=False,
                conversation__user_id__in=uid_list,
            )
            .values("conversation__user_id")
            .annotate(last=Max("created_at"))
            .values_list("conversation__user_id", "last")
        )
        for wallet in wallets:
            wallet.last_active = last_seen.get(wallet.user_id)
        return wallets

    def _get_orphaned_wallets(self) -> list:
        """Wallets with balance > 0 and no LLM messages ever (never activated)."""
        ever_active_ids = set(
            Message.active_objects.filter(llm__isnull=False)
            .values_list("conversation__user_id", flat=True)
            .distinct()
        )
        return list(
            Wallet.objects.filter(balance__gt=Decimal("0"))
            .exclude(user_id__in=ever_active_ids)
            .select_related("user")
            .order_by("-balance")[:10]
        )

    def _get_daily_trend_json(self, period: str) -> str:
        cutoff, _ = _period_cutoff(period)
        # For "all time" cap the chart at 90 days so it stays readable
        if cutoff is None:
            cutoff = timezone.now() - timedelta(days=90)

        msg_rows = (
            Message.active_objects.filter(llm__isnull=False, created_at__gte=cutoff)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                daily_input=Sum("input_tokens"),
                daily_output=Sum("output_tokens"),
                daily_calls=Count("id"),
                daily_users=Count("conversation__user", distinct=True),
            )
            .order_by("date")
        )
        spend_by_date = {
            row["date"]: float(row["c"] or 0)
            for row in Transaction.objects.filter(
                type=TransactionTypeChoice.DEBIT,
                created_at__gte=cutoff,
            )
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(c=Sum("amount"))
        }
        return json.dumps(
            [
                {
                    "date": str(row["date"]),
                    "cost": spend_by_date.get(row["date"], 0),
                    "calls": row["daily_calls"],
                    "tokens": (row["daily_input"] or 0) + (row["daily_output"] or 0),
                    "users": row["daily_users"],
                }
                for row in msg_rows
            ]
        )

    def _build_model_chart_json(self, breakdown: list) -> str:
        return json.dumps(
            [
                {
                    "name": row["llm_name"],
                    "cost": float(row["total_cost"] or 0),
                    "tokens": row["total_tokens"],
                    "calls": row["llm_calls"],
                }
                for row in breakdown
            ]
        )
