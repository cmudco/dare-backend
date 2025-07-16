from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from users.models import User, AccessCodeGroup
from users.constants import VectorDBChoice, AuthSourceChoice


class UserInline(admin.TabularInline):
    model = User
    fields = ('email', 'first_name', 'last_name', 'is_active', 'is_staff', 'date_joined')
    readonly_fields = ('email', 'first_name', 'last_name', 'is_staff', 'date_joined')
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AccessCodeGroup)
class AccessCodeGroupAdmin(admin.ModelAdmin):
    list_display = ('access_code', 'usage_display', 'is_active', 'user_count', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('access_code',)
    readonly_fields = ('current_usage', 'created_at', 'updated_at')
    list_editable = ('is_active',) 
    inlines = [UserInline]

    fieldsets = (
        (None, {
            'fields': ('access_code', 'max_capacity', 'is_active')
        }),
        (_('Usage Statistics'), {
            'fields': ('current_usage',),
            'classes': ('collapse',)
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def usage_display(self, obj):
        """Display usage in a more readable format"""
        percentage = (obj.current_usage / obj.max_capacity * 100) if obj.max_capacity > 0 else 0
        return f"{obj.current_usage}/{obj.max_capacity} ({percentage:.1f}%)"
    usage_display.short_description = "Usage"

    def user_count(self, obj):
        """Display the number of users in this group"""
        return obj.users.count()
    user_count.short_description = "Users Count"

    def get_list_display_links(self, request, list_display):
        """Make access_code clickable"""
        return ('access_code',)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.current_usage > 0:
            return False
        return super().has_delete_permission(request, obj)


class UserAdmin(DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("email", "password", "is_active", "is_staff", "is_superuser")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                )
            },
        ),
        (_("Access Control"), {"fields": ("access_code_group",)}),
        (_("Vector Database Settings"), {"fields": ("vector_db",)}),
        (_("Platform Settings"), {
            "fields": ("auth_source", "is_dare_accessible", "is_socratic_books_accessible")
        }),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "vector_db", "auth_source", "is_dare_accessible", "is_socratic_books_accessible", "is_superuser", "is_staff", "is_active"),
            },
        ),
    )
    list_display = ("email", "is_staff", "is_active", "is_superuser", "access_code_group", "vector_db", "auth_source", "is_dare_accessible", "is_socratic_books_accessible")
    list_filter = ("is_staff", "is_superuser", "is_active", "vector_db", "access_code_group", "auth_source", "is_dare_accessible", "is_socratic_books_accessible")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)


admin.site.register(User, UserAdmin)
