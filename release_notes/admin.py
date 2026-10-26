from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import ReleaseNote, ReleaseNoteItem
from .services.release_note_import import (
    ReleaseNoteImportError,
    import_release_note,
    parse_release_note_json,
)


class ReleaseNoteImportForm(forms.Form):
    payload = forms.CharField(
        label=_("Release note JSON"),
        widget=forms.Textarea(attrs={"rows": 18, "cols": 100, "spellcheck": "false"}),
    )
    publish = forms.BooleanField(
        label=_("Publish now"),
        required=False,
        help_text=_("Leave unticked to create a draft you can review first."),
    )


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

    def get_urls(self):
        return [
            path(
                "import-json/",
                self.admin_site.admin_view(self.import_json_view),
                name="release_notes_releasenote_import_json",
            ),
            *super().get_urls(),
        ]

    def import_json_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ReleaseNoteImportForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                note = import_release_note(
                    parse_release_note_json(form.cleaned_data["payload"]),
                    publish=form.cleaned_data["publish"],
                )
            except ReleaseNoteImportError as error:
                form.add_error("payload", str(error))
            else:
                self.message_user(
                    request,
                    _("Imported release %(version)s.") % {"version": note.version},
                    messages.SUCCESS,
                )
                return redirect(
                    reverse("admin:release_notes_releasenote_change", args=[note.pk])
                )
        return TemplateResponse(
            request,
            "admin/release_notes/releasenote/import_json.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "form": form,
                "title": _("Import release note from JSON"),
            },
        )

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
