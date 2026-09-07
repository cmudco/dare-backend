from django.contrib import admin

from .models import File, FileShare, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("label", "user")
    search_fields = ("label", "user__email")
    list_filter = ("user",)


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "file_type",
        "status",
        "processing_stage",
        "parser_name",
        "parser_fallback_details",
        "size",
        "syftbox_etag",
        "display_tags",
        "storage_backend",
        "source_file",
        "created_at",
        "vector_db_source",
    )
    search_fields = ("name", "user__email", "file_type", "syftbox_etag")
    list_filter = (
        "file_type",
        "status",
        "processing_stage",
        "parser_name",
        "created_at",
        "vector_db_source",
        "storage_backend",
    )
    ordering = ("-created_at",)
    raw_id_fields = ("source_file",)
    readonly_fields = ("parser_fallback_details",)

    @admin.display(description="Parser fallback")
    def parser_fallback_details(self, obj):
        fallback = (obj.document_model or {}).get("parser_fallback") or {}
        source = fallback.get("from")
        if not source:
            return "—"
        reason = fallback.get("reason") or "Unknown parser failure"
        return f"{source} → {obj.parser_name or 'unknown'}: {reason}"

    def display_tags(self, obj):
        """Helper method to display tags as a comma-separated string"""
        return ", ".join([tag.label for tag in obj.tags.all()])

    display_tags.short_description = "Tags"


@admin.register(FileShare)
class FileShareAdmin(admin.ModelAdmin):
    list_display = ("file", "shared_by", "shared_with", "created_at")
    search_fields = ("file__name", "shared_by__email", "shared_with__email")
    list_filter = ("created_at",)
    raw_id_fields = ("file", "shared_by", "shared_with")
