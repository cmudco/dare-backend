from django.contrib import admin
from .models import Prompt

@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_active", "created_at")
    search_fields = ("title", "content", "user__email")
    list_filter = ("is_active", "created_at")
    ordering = ("-created_at",)