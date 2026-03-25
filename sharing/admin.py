from django.contrib import admin

from sharing.models import SharedItem


@admin.register(SharedItem)
class SharedItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "content_type",
        "object_id",
        "shared_by",
        "shared_with",
        "created_at",
    )
    list_filter = ("content_type",)
    search_fields = ("shared_by__email", "shared_with__email")
    raw_id_fields = ("shared_by", "shared_with")
