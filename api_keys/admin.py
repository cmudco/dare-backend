from django.contrib import admin
from api_keys.models import UserProviderAPIKey


@admin.register(UserProviderAPIKey)
class UserProviderAPIKeyAdmin(admin.ModelAdmin):
    """
    Admin interface for managing user-provided API keys.

    Features:
    - Display masked API keys for security
    - Filter by user and provider
    - Search by user email
    - Read-only display of masked keys
    """
    list_display = (
        'user_email',
        'provider_display',
        'has_key_display',
        'masked_key_display',
        'is_active',
        'created_at',
        'updated_at'
    )
    list_filter = ('provider', 'is_active', 'created_at')
    search_fields = ('user__email', 'user__first_name', 'user__last_name')
    list_editable = ('is_active',)
    ordering = ('user__email', 'provider')
    readonly_fields = ('masked_key_display', 'created_at', 'updated_at')

    fieldsets = (
        (None, {
            'fields': ('user', 'provider', 'api_key', 'is_active'),
            'description': 'Manage user-provided API keys. Keys are encrypted in the database.'
        }),
        ('Key Information', {
            'fields': ('masked_key_display',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def user_email(self, obj):
        """Display user's email"""
        return obj.user.email
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'

    def provider_display(self, obj):
        """Display provider name with proper capitalization"""
        return obj.get_provider_display()
    provider_display.short_description = 'Provider'
    provider_display.admin_order_field = 'provider'

    def has_key_display(self, obj):
        """Display whether the user has set an API key"""
        if obj.has_key:
            return '✓ Set'
        return '✗ Not Set'
    has_key_display.short_description = 'Key Status'

    def masked_key_display(self, obj):
        """Display masked version of API key"""
        masked = obj.get_masked_key()
        if masked:
            return f'{masked}'
        return 'No key set'
    masked_key_display.short_description = 'Masked API Key'

    def get_queryset(self, request):
        """Optimize queryset with select_related to reduce queries"""
        qs = super().get_queryset(request)
        return qs.select_related('user')
