from django.contrib import admin
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.helpers.admin_utils import (
    render_empty_placeholder,
    render_feedback_icon,
    render_link,
    render_tooltip_span,
    truncate_text,
)
from .models import LLM, Conversation, Message, ModelGroup
from .proxy_models import MessageWithFeedback

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

@admin.register(MessageWithFeedback)
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