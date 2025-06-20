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
    list_display = ('user', 'display_amount', 'type', 'message', 'created_at','amount')
    list_filter = ('type', 'created_at')
    search_fields = ('user__email', 'message')
    date_hierarchy = 'created_at'
