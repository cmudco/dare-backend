from django.contrib import admin

from .models import Prompt, PublishedPrompt


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_active", "created_at")
    search_fields = ("title", "content", "user__email")
    list_filter = ("is_active", "created_at")
    ordering = ("-created_at",)


@admin.register(PublishedPrompt)
class PublishedPromptAdmin(admin.ModelAdmin):
    list_display = ("prompt", "get_author", "published_at", "is_active")
    search_fields = ("prompt__title", "prompt__user__email", "description")
    list_filter = ("is_active", "published_at")
    ordering = ("-published_at",)

    def get_author(self, obj):
        return obj.prompt.user.email
    get_author.short_description = "Author"