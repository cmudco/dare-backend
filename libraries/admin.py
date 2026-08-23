from django.contrib import admin

from libraries.models import SharedLibrary, UserLibraryAccess


@admin.register(SharedLibrary)
class SharedLibraryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "backend",
        "namespace",
        "embedding_model",
        "dims",
        "object_count",
        "is_available",
    )
    list_filter = ("backend", "is_available")
    search_fields = ("name", "slug", "curator")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserLibraryAccess)
class UserLibraryAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "library", "created_at")
    search_fields = ("user__username", "library__slug")
    list_filter = ("library",)
