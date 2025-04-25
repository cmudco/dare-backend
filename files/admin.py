from django.contrib import admin
from .models import File, Tag

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("label", "user")
    search_fields = ("label", "user__email")
    list_filter = ("user",)

@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "file_type", "size", "display_tags", "created_at", "vector_db_source")
    search_fields = ("name", "user__email", "file_type")
    list_filter = ("file_type", "created_at", "vector_db_source")
    ordering = ("-created_at",)

    def display_tags(self, obj):
        """Helper method to display tags as a comma-separated string"""
        return ", ".join([tag.label for tag in obj.tags.all()])
    display_tags.short_description = "Tags"
