from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils.html import format_html

from .models import LLM, Conversation, Message, ModelGroup

User = get_user_model()

@admin.register(LLM)
class LLMAdmin(admin.ModelAdmin):
    list_display = ("name", "identifier", "provider")
    search_fields = ("name", "identifier")
    list_filter = ("provider",)

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("conversation_id", "user", "title", "sort_order", "created_at")
    search_fields = ("conversation_id", "user__email", "title")
    list_filter = ("created_at",)
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

# Dedicated admin view for messages with feedback
class MessageWithFeedbackAdmin(admin.ModelAdmin):
    """Dedicated admin view showing only messages with feedback"""
    list_display = ("id", "short_message", "conversation_link", "sender_name", "feedback_indicator", "feedback_preview", "created_at")
    search_fields = ("message", "conversation__conversation_id", "conversation__title", "sender", "feedback_text")
    list_filter = ("sender_type", "feedback_type", "created_at", "llm")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "input_tokens", "output_tokens", "cost")
    list_per_page = 50

    fieldsets = (
        ("Message Info", {
            "fields": ("conversation", "sender_type", "sender", "message", "llm")
        }),
        ("Feedback", {
            "fields": ("feedback_type", "feedback_text"),
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
        msg = obj.message[:50] + "..." if len(obj.message) > 50 else obj.message
        return format_html('<span title="{}">{}</span>', obj.message, msg)
    short_message.short_description = "Message"

    def conversation_link(self, obj):
        return format_html(
            '<a href="/admin/conversations/conversation/{}/change/">{}</a>',
            obj.conversation.id,
            obj.conversation.title or obj.conversation.conversation_id
        )
    conversation_link.short_description = "Conversation"

    def feedback_indicator(self, obj):
        if obj.feedback_type == 'like':
            return format_html('<span style="color: green; font-size: 18px;">👍</span>')
        elif obj.feedback_type == 'dislike':
            return format_html('<span style="color: red; font-size: 18px;">👎</span>')
        return format_html('<span style="color: gray;">—</span>')
    feedback_indicator.short_description = "Feedback"
    feedback_indicator.admin_order_field = "feedback_type"

    def feedback_preview(self, obj):
        if obj.feedback_text:
            preview = obj.feedback_text[:60] + "..." if len(obj.feedback_text) > 60 else obj.feedback_text
            return format_html('<span title="{}">{}</span>', obj.feedback_text, preview)
        return format_html('<span style="color: gray; font-style: italic;">No text</span>')
    feedback_preview.short_description = "Feedback Text"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Only show messages that have feedback and optimize queries
        return qs.filter(feedback_type__isnull=False).select_related('conversation', 'llm', 'conversation__user')

# Register the feedback-specific view with a proxy model
class MessageWithFeedback(Message):
    class Meta:
        proxy = True
        verbose_name = "Message with Feedback"
        verbose_name_plural = "Messages with Feedback"

admin.site.register(MessageWithFeedback, MessageWithFeedbackAdmin)

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