from django.contrib import admin

from billing.models import Wallet, Transaction

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

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'display_amount', 'type', 'platform', 'billing_mode', 'llm_name', 'message', 'created_at', 'amount')
    list_filter = ('type', 'platform', 'billing_mode', 'created_at', 'llm_name')
    search_fields = ('user__email', 'message', 'llm_name')
    date_hierarchy = 'created_at'
    readonly_fields = ('llm_name', 'created_at', 'updated_at')
