from django.contrib import admin

from projects.models import PersonalProject


@admin.register(PersonalProject)
class PersonalProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "is_deleted", "updated_at")
    list_filter = ("is_deleted",)
    search_fields = ("name", "user__email")
    raw_id_fields = ("user",)
    filter_horizontal = ("files", "folders", "libraries")
