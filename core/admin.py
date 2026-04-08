from django.contrib import admin

from core.models import DareConfig


@admin.register(DareConfig)
class DareConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project_email",
        "is_active",
        "is_deleted",
        "updated_at",
        "created_at",
    )
    search_fields = ("project_email",)
    list_filter = ("is_active", "is_deleted", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
