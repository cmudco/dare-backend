from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from billing.constants import TransactionSourceChoice, TransactionTypeChoice
from billing.group_wallet_service import (
    FundGroupBudgetRequest,
    GroupWalletService,
    UpdateGroupPolicyRequest,
)
from billing.models import (
    GroupWallet,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    Wallet,
)
from billing.services import TransactionExportService


class TokenUsageFilter(admin.SimpleListFilter):
    """Filter transactions by token usage ranges."""
    title = 'token usage'
    parameter_name = 'token_usage'

    def lookups(self, request, model_admin):
        return (
            ('low', 'Low (0-1K tokens)'),
            ('medium', 'Medium (1K-10K tokens)'),
            ('high', 'High (10K-50K tokens)'),
            ('very_high', 'Very High (50K+ tokens)'),
            ('zero', 'Zero tokens'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'zero':
            return queryset.filter(
                Q(input_tokens=0, output_tokens=0) |
                Q(input_tokens__isnull=True) |
                Q(output_tokens__isnull=True)
            )
        elif self.value() == 'low':
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False) &
                Q(input_tokens__gte=0, input_tokens__lt=1000) &
                Q(output_tokens__gte=0, output_tokens__lt=1000)
            ).exclude(
                Q(input_tokens=0) & Q(output_tokens=0)
            )
        elif self.value() == 'medium':
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=1000, input_tokens__lt=10000) |
                Q(output_tokens__gte=1000, output_tokens__lt=10000)
            )
        elif self.value() == 'high':
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=10000, input_tokens__lt=50000) |
                Q(output_tokens__gte=10000, output_tokens__lt=50000)
            )
        elif self.value() == 'very_high':
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=50000) | Q(output_tokens__gte=50000)
            )
        return queryset


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "display_balance", "last_refill_at", "created_at", "updated_at")
    search_fields = ("user__email",)
    list_filter = ("user__is_active",)
    ordering = ("-balance",)
    readonly_fields = ("balance", "last_refill_at", "created_at", "updated_at")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user':
            kwargs['queryset'] = db_field.related_model.objects.order_by('email')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'display_amount',
        'type',
        'source',
        'related_group',
        'platform',
        'billing_mode',
        'llm_name',
        'input_tokens',
        'output_tokens',
        'total_tokens_display',
        'message',
        'created_at',
    )
    list_filter = (
        'type',
        'source',
        'platform',
        'billing_mode',
        'created_at',
        'llm_name',
        TokenUsageFilter,
    )
    search_fields = ('user__email', 'message', 'llm_name', 'related_group__access_code')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = (
        'display_amount',
        'llm_name',
        'input_tokens',
        'output_tokens',
        'total_tokens_display',
        'created_at',
        'updated_at',
    )
    raw_id_fields = ('related_group', 'related_transaction')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user':
            kwargs['queryset'] = db_field.related_model.objects.order_by('email')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def display_amount(self, obj):
        return obj.display_amount if obj else "N/A"
    display_amount.short_description = 'Amount'
    actions = ['export_transactions_to_csv']

    fieldsets = (
        ('Transaction Info', {
            'fields': ('user', 'type', 'source', 'platform', 'billing_mode', 'message')
        }),
        ('Group Linkage', {
            'fields': ('related_group', 'related_transaction'),
            'classes': ('collapse',),
            'description': 'Links to the AccessCodeGroup this transaction belongs to and any paired transaction.',
        }),
        ('Billing Details', {
            'fields': ('amount', 'display_amount', 'llm', 'llm_name')
        }),
        ('Token Usage', {
            'fields': ('input_tokens', 'output_tokens', 'total_tokens_display'),
            'description': 'Token consumption metrics for this transaction'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def total_tokens_display(self, obj):
        if obj.input_tokens is not None and obj.output_tokens is not None:
            total = obj.input_tokens + obj.output_tokens
            return f"{total:,}"
        return "N/A"
    total_tokens_display.short_description = 'Total Tokens'
    total_tokens_display.admin_order_field = 'input_tokens'

    def export_transactions_to_csv(self, request, queryset):
        return TransactionExportService.export_to_csv(queryset)

    export_transactions_to_csv.short_description = 'Export selected transactions to CSV'


# --- System refill policy (singleton) -------------------------------------

@admin.register(SystemRefillPolicy)
class SystemRefillPolicyAdmin(admin.ModelAdmin):
    list_display = ('refill_amount', 'refill_period_days', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

    def has_add_permission(self, request):
        # Singleton — prevent additional rows
        return not SystemRefillPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# --- Group wallet ---------------------------------------------------------

class GroupWalletActionForm(ActionForm):
    amount = forms.DecimalField(
        required=False,
        min_value=Decimal('0.01'),
        max_digits=15,
        decimal_places=6,
        help_text="Amount (USD). Required for 'fund' action; optional for 'set policy'."
    )
    period_days = forms.IntegerField(
        required=False,
        min_value=1,
        help_text="Refill period in days. Optional."
    )
    note = forms.CharField(required=False, max_length=255)


@admin.register(GroupWallet)
class GroupWalletAdmin(admin.ModelAdmin):
    list_display = ('group', 'group_owner_display', 'budget_balance', 'refill_amount',
                    'refill_period_days', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('group__access_code', 'group__group_owner__email')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('group',)
    action_form = GroupWalletActionForm
    actions = ['fund_budget_action', 'set_policy_action']

    def group_owner_display(self, obj):
        owner = obj.group.group_owner if obj.group else None
        return owner.email if owner else '—'
    group_owner_display.short_description = 'Group Owner'

    def save_model(self, request, obj, form, change):
        """Direct edits to budget_balance auto-log an audit Transaction so the
        group's funding history stays reconstructable regardless of which path
        (action vs. direct edit) the admin used."""
        old_balance = Decimal('0')
        if change and obj.pk:
            old_balance = (
                GroupWallet.objects.filter(pk=obj.pk)
                .values_list('budget_balance', flat=True)
                .first()
                or Decimal('0')
            )
        super().save_model(request, obj, form, change)
        new_balance = obj.budget_balance or Decimal('0')
        delta = new_balance - old_balance
        if delta != 0:
            sign = '+' if delta > 0 else ''
            Transaction.objects.create(
                user=request.user,
                amount=Decimal('0'),
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
        amount_raw = request.POST.get('amount')
        note = request.POST.get('note') or 'Admin budget top-up'
        try:
            amount = Decimal(amount_raw)
        except Exception:
            self.message_user(request, "Please provide a valid amount.", level=messages.ERROR)
            return
        funded = 0
        for gw in queryset:
            try:
                GroupWalletService.fund_group_budget(FundGroupBudgetRequest(
                    group_wallet_id=gw.id,
                    actor=request.user,
                    amount=amount,
                    note=note,
                ))
                funded += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, f"Skipped {gw.group.access_code}: {exc}", level=messages.WARNING)
        self.message_user(
            request,
            f"Funded {funded} group budget(s) with ${amount}.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Set refill policy on selected group wallet(s)")
    def set_policy_action(self, request, queryset):
        amount_raw = request.POST.get('amount')
        period_raw = request.POST.get('period_days')
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
            self.message_user(request, "Provide amount, period_days, or both.", level=messages.ERROR)
            return

        changed = 0
        for gw in queryset:
            try:
                GroupWalletService.update_group_policy(UpdateGroupPolicyRequest(
                    group_wallet_id=gw.id,
                    owner=request.user,
                    refill_amount=amount,
                    refill_period_days=period,
                ))
                changed += 1
            except (PermissionDenied, ValidationError) as exc:
                self.message_user(request, f"Skipped {gw.group.access_code}: {exc}", level=messages.WARNING)
        self.message_user(request, f"Updated policy on {changed} group wallet(s).", level=messages.SUCCESS)


class GroupWalletInline(admin.StackedInline):
    """Inline on AccessCodeGroupAdmin so admins can configure the wallet and set
    the initial budget at group creation time. Direct edits here are audited via
    AccessCodeGroupAdmin.save_formset."""
    model = GroupWallet
    can_delete = False
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    fk_name = 'group'
    fieldsets = (
        (None, {
            'fields': ('budget_balance', 'refill_amount', 'refill_period_days', 'is_active'),
            'description': (
                'Budget fund the group; drained by scheduled refills and one-off '
                'allocations. Leave refill fields blank to inherit the system default.'
            ),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


# --- User refill overrides ------------------------------------------------

@admin.register(UserRefillOverride)
class UserRefillOverrideAdmin(admin.ModelAdmin):
    list_display = ('user', 'refill_amount', 'refill_period_days', 'set_by', 'updated_at')
    search_fields = ('user__email', 'reason')
    raw_id_fields = ('user', 'set_by')
    readonly_fields = ('created_at', 'updated_at')


class UserRefillOverrideInline(admin.StackedInline):
    """Inline so admins can see/edit override directly on UserAdmin."""
    model = UserRefillOverride
    can_delete = True
    extra = 0
    fk_name = 'user'
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('refill_amount', 'refill_period_days', 'reason', 'set_by',
                       'created_at', 'updated_at'),
        }),
    )
