from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from core.constants import BackgroundModelSource, BackgroundTask
from core.models import DareConfig, PlatformSettings
from core.services.background_model_service import (
    BackgroundModelUnavailable,
    platform_background_model,
)


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


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {"fields": ("background_model",)}),
        (
            _("Per-task overrides (optional)"),
            {
                "fields": tuple(PlatformSettings.TASK_FIELDS.values()),
                "description": _(
                    "Pick a different model for one task, for example a cheaper "
                    "model for titles. Users who chose their own background model "
                    "in Settings, and LiteLLM keys with a background model, keep "
                    "using theirs."
                ),
            },
        ),
        (_("In use now"), {"fields": ("models_in_use", "updated_at")}),
    )
    readonly_fields = ("models_in_use", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings, _created = PlatformSettings.objects.get_or_create(
            pk=PlatformSettings.SINGLETON_ID
        )
        return redirect(
            reverse("admin:core_platformsettings_change", args=[settings.pk])
        )

    @admin.display(description=_("Platform models"))
    def models_in_use(self, obj):
        rows = []
        for task in BackgroundTask:
            try:
                model, source = platform_background_model(task)
                line = _("%(name)s (%(identifier)s) · %(source)s") % {
                    "name": model.name,
                    "identifier": model.identifier,
                    "source": BackgroundModelSource(source).label,
                }
            except BackgroundModelUnavailable as error:
                line = str(error)
            rows.append((task.label, line))
        return format_html_join(mark_safe("<br>"), "{}: {}", rows)
