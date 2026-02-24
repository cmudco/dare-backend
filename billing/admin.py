from django.contrib import admin
from django.db.models import Q

from billing.models import Wallet, Transaction
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
            # Both input and output tokens should be less than 1000
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False) &
                Q(input_tokens__gte=0, input_tokens__lt=1000) &
                Q(output_tokens__gte=0, output_tokens__lt=1000)
            ).exclude(
                Q(input_tokens=0) & Q(output_tokens=0)
            )
        elif self.value() == 'medium':
            # Either input or output tokens between 1K-10K
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=1000, input_tokens__lt=10000) |
                Q(output_tokens__gte=1000, output_tokens__lt=10000)
            )
        elif self.value() == 'high':
            # Either input or output tokens between 10K-50K
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=10000, input_tokens__lt=50000) |
                Q(output_tokens__gte=10000, output_tokens__lt=50000)
            )
        elif self.value() == 'very_high':
            # Either input or output tokens >= 50K
            return queryset.filter(
                Q(input_tokens__isnull=False) &
                Q(output_tokens__isnull=False)
            ).filter(
                Q(input_tokens__gte=50000) | Q(output_tokens__gte=50000)
            )
        return queryset

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    """
    Admin interface for the Wallet model.
    """
    list_display = ("user", "display_balance", "created_at", "updated_at")
    search_fields = ("user__email",)
    list_filter = ("user__is_active",)
    ordering = ("-balance",)
    readonly_fields = ("balance", "created_at", "updated_at")

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
        'platform',
        'billing_mode',
        'llm_name',
        'input_tokens',
        'output_tokens',
        'total_tokens_display',
        'message',
        'created_at'
    )
    list_filter = (
        'type',
        'platform',
        'billing_mode',
        'created_at',
        'llm_name',
        TokenUsageFilter,
    )
    search_fields = ('user__email', 'message', 'llm_name')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = (
        'display_amount',
        'llm_name',
        'input_tokens',
        'output_tokens',
        'total_tokens_display',
        'created_at',
        'updated_at'
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user':
            kwargs['queryset'] = db_field.related_model.objects.order_by('email')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def display_amount(self, obj):
        """Display formatted amount from the model property."""
        return obj.display_amount if obj else "N/A"
    display_amount.short_description = 'Amount'
    actions = ['export_transactions_to_csv']

    fieldsets = (
        ('Transaction Info', {
            'fields': ('user', 'type', 'platform', 'billing_mode', 'message')
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
        """Display total tokens (input + output)."""
        if obj.input_tokens is not None and obj.output_tokens is not None:
            total = obj.input_tokens + obj.output_tokens
            return f"{total:,}"
        return "N/A"
    total_tokens_display.short_description = 'Total Tokens'
    total_tokens_display.admin_order_field = 'input_tokens'

    def export_transactions_to_csv(self, request, queryset):
        """
        Export selected transactions to CSV format matching frontend export structure.
        Respects any date filters applied in the admin interface.

        Uses TransactionExportService for consistent CSV generation.
        """
        return TransactionExportService.export_to_csv(queryset)

    export_transactions_to_csv.short_description = 'Export selected transactions to CSV'
