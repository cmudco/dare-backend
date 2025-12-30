from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse
from django import forms

from core.helpers.admin_utils import (
    render_code_block,
    render_empty_placeholder,
    render_feedback_icon,
    render_link,
    render_tooltip_span,
    truncate_text,
)
from .models import LLM, Conversation, Message, ModelGroup, ProviderAPIKey, ModelCardData, PublicFeedbackSourceCluster, PublicFeedbackSource
from .proxy_models import MessageWithFeedback

User = get_user_model()

@admin.register(LLM)
class LLMAdmin(admin.ModelAdmin):
    list_display = ("name", "identifier", "provider", "base_url_display")
    search_fields = ("name", "identifier", "base_url")
    list_filter = ("provider",)

    fieldsets = (
        ("Model Information", {
            "fields": ("name", "identifier", "description", "provider")
        }),
        ("Custom Endpoint (for CUSTOM provider)", {
            "fields": ("base_url",),
            "description": "Required when provider is set to 'CUSTOM'. Enter the base URL for OpenAI-compatible endpoints (e.g., https://litellm-dev.pace.gatech.edu:4000/v1)"
        }),
        ("Capabilities", {
            "fields": ("is_reasoning", "supports_vision", "is_image_generator")
        }),
        ("Pricing", {
            "fields": ("input_token_rate_per_million", "output_token_rate_per_million"),
            "classes": ("collapse",)
        }),
    )

    def base_url_display(self, obj):
        """Display base URL or indicate if using default provider endpoint"""
        if obj.base_url:
            return obj.base_url
        return "-"
    base_url_display.short_description = "Base URL"

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("conversation_id", "user", "title", "source", "sort_order", "created_at")
    search_fields = ("conversation_id", "user__email", "title")
    list_filter = ("created_at", "source")
    ordering = ("sort_order", "-created_at")
    list_editable = ("sort_order",)

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("short_message", "conversation", "sender_name", "sender_type", "created_at")
    search_fields = ("message", "conversation__conversation_id", "sender")
    list_filter = ("sender_type", "created_at")
    ordering = ("-created_at",)

    def short_message(self, obj):
        return obj.short_message
    short_message.short_description = "Message"

@admin.register(MessageWithFeedback)
class MessageWithFeedbackAdmin(admin.ModelAdmin):
    """Dedicated admin view showing only messages with feedback"""
    list_display = ("id", "short_message", "conversation_link", "sender_name", "feedback_indicator", "feedback_preview", "created_at")
    search_fields = ("message", "conversation__conversation_id", "conversation__title", "sender", "feedback_text")
    list_filter = ("sender_type", "feedback_type", "feedback_source", "created_at", "llm")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "input_tokens", "output_tokens", "cost")
    list_per_page = 50

    fieldsets = (
        ("Message Info", {
            "fields": ("conversation", "sender_type", "sender", "message", "llm")
        }),
        ("Feedback", {
            "fields": ("feedback_type", "feedback_text", "feedback_source"),
            "description": "User feedback for this message"
        }),
        ("Message History", {
            "fields": ("is_edited", "is_regenerated", "original_message"),
            "classes": ("collapse",)
        }),
        ("Usage & Metrics", {
            "fields": ("input_tokens", "output_tokens", "cost"),
            "classes": ("collapse",)
        }),
        ("Related Data", {
            "fields": ("files", "tags", "learning_progress_data"),
            "classes": ("collapse",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    filter_horizontal = ("files", "tags")

    def short_message(self, obj):
        preview = truncate_text(obj.message, 50)
        return render_tooltip_span(obj.message, preview)
    short_message.short_description = "Message"

    def conversation_link(self, obj):
        conversation = obj.conversation
        url = reverse("admin:conversations_conversation_change", args=[conversation.pk])
        text = conversation.title or conversation.conversation_id
        return render_link(url, text)
    conversation_link.short_description = "Conversation"

    def feedback_indicator(self, obj):
        return render_feedback_icon(obj.feedback_type)
    feedback_indicator.short_description = "Feedback"
    feedback_indicator.admin_order_field = "feedback_type"

    def feedback_preview(self, obj):
        if obj.feedback_text:
            preview = truncate_text(obj.feedback_text, 60)
            return render_tooltip_span(obj.feedback_text, preview)
        return render_empty_placeholder()
    feedback_preview.short_description = "Feedback Text"

@admin.register(ModelGroup)
class ModelGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "model_count", "user_count", "created_at")
    search_fields = ("name", "description")
    list_filter = ("is_active", "created_at")
    ordering = ("name",)
    list_editable = ("is_active",)

    filter_horizontal = ("allowed_models",)

    fieldsets = (
        (None, {
            "fields": ("name", "description", "is_active")
        }),
        ("Models", {
            "fields": ("allowed_models",)
        }),
    )

    def model_count(self, obj):
        return obj.allowed_models.count()
    model_count.short_description = "Models"

    def user_count(self, obj):
        # Count users linked via AccessCodeGroup -> ModelGroup using module-level User
        return User.objects.filter(access_code_group__model_group=obj).count()
    user_count.short_description = "Users"


class SecureProviderAPIKeyForm(forms.ModelForm):
    """
    Custom form for server API key management that prevents viewing of stored keys.

    SECURITY: Keys are write-only after being saved. Even admins cannot retrieve them.
    """
    new_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Enter new API key to update (leave blank to keep current key)',
            'autocomplete': 'off',
            'size': '50'
        }),
        label='API Key',
        help_text='⚠️ Enter a new API key to update. Leave blank to keep the existing key. '
                  'Keys are encrypted and cannot be retrieved once saved.'
    )

    class Meta:
        model = ProviderAPIKey
        fields = ['provider', 'is_active']

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


@admin.register(ProviderAPIKey)
class ProviderAPIKeyAdmin(admin.ModelAdmin):
    """
    Admin interface for managing provider API keys.

    SECURITY FEATURES:
    - API keys are NEVER displayed in full (even to superusers)
    - Only masked versions are shown (e.g., ***xyz123)
    - API key updates use password-style input
    - Keys are encrypted at rest using AES-256
    - These are SERVER keys used by the platform, not user keys

    Features:
    - Display masked API keys for security
    - Allow activation/deactivation of keys
    - Search and filter by provider
    - Prevent deletion of active keys by non-superusers
    """
    form = SecureProviderAPIKeyForm

    list_display = ("provider_display", "masked_key_display", "is_active", "created_at", "updated_at")
    list_filter = ("provider", "is_active", "created_at")
    search_fields = ("provider",)
    list_editable = ("is_active",)
    ordering = ("provider",)

    fieldsets = (
        (None, {
            "fields": ("provider", "is_active"),
            "description": "🔒 SECURITY: Server API keys are encrypted and cannot be viewed once saved."
        }),
        ("API Key Management", {
            "fields": ("masked_key_display", "new_api_key"),
            "description": "Current key is shown masked. Enter a new key below to update it."
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    readonly_fields = ("masked_key_display", "created_at", "updated_at")

    def provider_display(self, obj):
        """Display provider name with icon/badge"""
        provider_icons = {
            "openai": "🤖",
            "claude": "🧠",
            "gemini": "✨",
            "llama": "🦙",
        }
        icon = provider_icons.get(obj.provider, "🔑")
        return f"{icon} {obj.get_provider_display()}"
    provider_display.short_description = "Provider"

    def masked_key_display(self, obj):
        """Display masked API key showing only last 4 characters"""
        masked = obj.get_masked_key()
        return render_code_block(masked)
    masked_key_display.short_description = "API Key"

    def has_delete_permission(self, request, obj=None):
        """Only superusers can delete API keys"""
        if obj and obj.is_active and not request.user.is_superuser:
            return False
        return request.user.is_superuser

    def get_readonly_fields(self, request, obj=None):
        """Make provider field readonly when editing existing keys"""
        if obj:  # Editing existing key
            return self.readonly_fields + ("provider",)
        return self.readonly_fields


@admin.register(ModelCardData)
class ModelCardDataAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_name', 'slug', 'llm', 'updated_at')
    list_filter = ('provider_name',)
    search_fields = ('name', 'slug', 'name_variants')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('llm',)

class PublicFeedbackSourceInline(admin.TabularInline):
    model = PublicFeedbackSource
    extra = 0
    fields = ['source_type', 'title', 'url', 'page_date']
    readonly_fields = ['title', 'url', 'page_date', 'originating_query']


@admin.register(PublicFeedbackSourceCluster)
class PublicFeedbackSourceClusterAdmin(admin.ModelAdmin):
    list_display = ['cluster_index', 'canonical_title', 'model_card', 'source_count', 'created_at']
    list_filter = ['model_card', 'created_at']
    search_fields = ['canonical_title', 'canonical_url', 'identifier']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [PublicFeedbackSourceInline]

    def source_count(self, obj):
        return obj.sources.count()
    source_count.short_description = "Sources"


@admin.register(PublicFeedbackSource)
class PublicFeedbackSourceAdmin(admin.ModelAdmin):
    list_display = ['title', 'source_type', 'cluster', 'page_date']
    list_filter = ['source_type', 'cluster__model_card']
    search_fields = ['title', 'url', 'snippet']
    readonly_fields = ['created_at']
