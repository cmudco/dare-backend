from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import ReleaseNote, ReleaseNoteItem


class ReleaseNoteItemInline(admin.StackedInline):
    model = ReleaseNoteItem
    extra = 1
    fields = ["position", "kind", "title", "body", "link_path"]


@admin.register(ReleaseNote)
class ReleaseNoteAdmin(admin.ModelAdmin):
    list_display = ["version", "title", "source", "status_display", "published_at"]
    list_filter = ["source"]
    search_fields = ["version", "title", "summary"]
    readonly_fields = ["created_at", "updated_at"]
    fields = [
        "source",
        "version",
        "title",
        "summary",
        "published_at",
        "is_deleted",
        "created_at",
        "updated_at",
    ]
    inlines = [ReleaseNoteItemInline]
    actions = ["publish_now"]

    @admin.display(description=_("Status"))
    def status_display(self, obj):
        return obj.status.label

    @admin.action(description=_("Publish selected releases now"))
    def publish_now(self, request, queryset):
        count = queryset.filter(published_at__isnull=True).update(
            published_at=timezone.now()
        )
        self.message_user(
            request,
            _("Published %(count)d release(s).") % {"count": count},
            messages.SUCCESS,
        )
