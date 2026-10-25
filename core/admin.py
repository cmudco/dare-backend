from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

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
    fields = ("background_model", "background_model_in_use", "updated_at")
    readonly_fields = ("background_model_in_use", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings, _ = PlatformSettings.objects.get_or_create(
            pk=PlatformSettings.SINGLETON_ID
        )
        return redirect(
            reverse("admin:core_platformsettings_change", args=[settings.pk])
        )

    @admin.display(description=_("In use now"))
    def background_model_in_use(self, obj):
        try:
            model = platform_background_model()
        except BackgroundModelUnavailable as error:
            return str(error)
        source = (
            _("this setting")
            if obj.background_model_id == model.pk
            else _("the BACKGROUND_MODEL environment variable")
        )
        return _("%(name)s (%(identifier)s), from %(source)s") % {
            "name": model.name,
            "identifier": model.identifier,
            "source": source,
        }
