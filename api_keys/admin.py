from django.contrib import admin
from django import forms
from api_keys.models import UserProviderAPIKey


class SecureAPIKeyForm(forms.ModelForm):
    """
    Custom form for API key management that prevents viewing of stored keys.

    Features:
    - API key field is write-only (password input)
    - Shows masked version of existing key
    - Prevents accidental key disclosure
    """
    new_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter new API key to update (leave blank to keep current key)',
            'autocomplete': 'off'
        }),
        label='Update API Key',
        help_text='Enter a new API key to update. Leave blank to keep the existing key. '
                  'Keys are encrypted and cannot be retrieved once saved.'
    )

    class Meta:
        model = UserProviderAPIKey
        fields = ['user', 'provider', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove the original api_key field from the form
        if 'api_key' in self.fields:
            del self.fields['api_key']

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Only update the API key if a new one was provided
        new_key = self.cleaned_data.get('new_api_key')
        if new_key:
            instance.api_key = new_key

        if commit:
            instance.save()
        return instance


@admin.register(UserProviderAPIKey)
class UserProviderAPIKeyAdmin(admin.ModelAdmin):
    """
    Admin interface for managing user-provided API keys.

    SECURITY FEATURES:
    - API keys are NEVER displayed in full (even to admins)
    - Only masked versions are shown (e.g., sk-proj-***********xyz123)
    - API key updates use password-style input
    - Keys are encrypted at rest using AES-256

    Features:
    - Display masked API keys for security
    - Filter by user and provider
    - Search by user email
    - Write-only API key updates
    """
    form = SecureAPIKeyForm

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
    readonly_fields = ('masked_key_display', 'has_key_display', 'created_at', 'updated_at')

    fieldsets = (
        (None, {
            'fields': ('user', 'provider', 'is_active'),
            'description': '⚠️ SECURITY: API keys are encrypted and cannot be viewed once saved.'
        }),
        ('API Key Management', {
            'fields': ('masked_key_display', 'new_api_key'),
            'description': 'Current key is shown masked. Enter a new key below to update it.'
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
