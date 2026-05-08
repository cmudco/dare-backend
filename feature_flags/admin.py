from django.contrib import admin

from feature_flags.models import (
    FeatureFlag,
    GroupFeatureOverride,
    UserFeatureOverride,
)


class GroupFeatureOverrideInline(admin.TabularInline):
    model = GroupFeatureOverride
    extra = 0
    autocomplete_fields = ("group",)
    fields = ("group", "enabled")


class UserFeatureOverrideInline(admin.TabularInline):
    model = UserFeatureOverride
    extra = 0
    autocomplete_fields = ("user",)
    fields = ("user", "enabled")


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "default_enabled",
        "group_override_count",
        "user_override_count",
        "updated_at",
    )
    list_filter = ("default_enabled",)
    search_fields = ("key", "description")
    readonly_fields = ("created_at", "updated_at")
    inlines = (GroupFeatureOverrideInline, UserFeatureOverrideInline)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("group_overrides", "user_overrides")

    @admin.display(description="Group overrides")
    def group_override_count(self, obj):
        return obj.group_overrides.count()

    @admin.display(description="User overrides")
    def user_override_count(self, obj):
        return obj.user_overrides.count()


@admin.register(GroupFeatureOverride)
class GroupFeatureOverrideAdmin(admin.ModelAdmin):
    list_display = ("flag", "group", "enabled", "updated_at")
    list_filter = ("enabled", "flag")
    autocomplete_fields = ("flag", "group")
    search_fields = ("flag__key", "group__access_code")


@admin.register(UserFeatureOverride)
class UserFeatureOverrideAdmin(admin.ModelAdmin):
    list_display = ("flag", "user", "enabled", "updated_at")
    list_filter = ("enabled", "flag")
    autocomplete_fields = ("flag", "user")
    search_fields = ("flag__key", "user__email")
