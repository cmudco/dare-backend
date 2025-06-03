from django.contrib import admin

from .models import LLM, Conversation, Message

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

