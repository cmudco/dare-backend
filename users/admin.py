from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.contrib.admin.helpers import ActionForm
from django.utils import timezone
from datetime import timedelta

from users.models import User, AccessCodeGroup
from billing.services import WalletService
from django import forms
from decimal import Decimal
from users.constants import VectorDBChoice, AuthSourceChoice, RoleChoice
from users.filters import LastLoginFilter
from users.admin_constants import (
    LAST_LOGIN_DISPLAY_RULES,
    LAST_LOGIN_DISPLAY_DEFAULT,
    ACTIVITY_LEVELS,
    ACTIVITY_NEVER_STATE,
)
from core.helpers.admin_utils import render_span, render_status_badge


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
    list_display = ('access_code', 'default_role', 'model_group', 'initial_wallet_credit', 'usage_display', 'expiration_status', 'is_active', 'user_count', 'created_at')
    list_filter = ('is_active', 'default_role', 'created_at', 'model_group')
    search_fields = ('access_code',)
    readonly_fields = ('current_usage', 'created_at', 'updated_at')
    list_editable = ('is_active',)
    inlines = [UserInline]
    
    class GroupCreditActionForm(ActionForm):
        amount = forms.DecimalField(
            required=True,
            min_value=Decimal('0.01'),
            max_digits=10,
            decimal_places=6,
            help_text="Amount to credit to each user in the selected access code groups (USD)"
        )
        note = forms.CharField(required=False, max_length=255)

    action_form = GroupCreditActionForm

    @admin.action(description="Credit all users in selected group(s)")
    def credit_groups_users(self, request, queryset):
        try:
            amount_str = request.POST.get('amount')
            note = request.POST.get('note') or 'Admin group credit'
            amount = Decimal(amount_str)
        except Exception:
            self.message_user(request, "Please provide a valid amount for crediting.", level=messages.ERROR)
            return

        user_ids_seen = set()
        credited = 0
        for group in queryset:
            for user in group.users.all():
                if user.id in user_ids_seen:
                    continue
                try:
                    WalletService.add_topup(user, amount=amount, message=f"{note} (ACG: {group.access_code})")
                    credited += 1
                    user_ids_seen.add(user.id)
                except Exception:
                    continue

        self.message_user(request, f"Credited {credited} user(s) across {queryset.count()} group(s) with ${amount}.", level=messages.SUCCESS)

    fieldsets = (
        (None, {
            'fields': ('access_code', 'max_capacity', 'is_active', 'expires_at', 'notes')
        }),
        (_('Role Assignment'), {
            'fields': ('default_role',),
            'description': (
                'Role assigned to users who register with this access code.<br><br>'
                '<strong>SUPERADMIN</strong> — Full DARE platform access + SocraticBooks creator + admin privileges.<br>'
                '<strong>SUPERVISOR</strong> — DARE platform access + cross-user bot/agent management in SocraticBooks + creator access.<br>'
                '<strong>RESEARCHER</strong> — DARE platform access + SocraticBooks creator (can create and manage books).<br>'
                '<strong>USER</strong> — DARE platform access + SocraticBooks student/consumer (can read and interact with books).<br>'
                '<strong>CREATOR</strong> — No DARE access + SocraticBooks creator (can create and manage books only).<br>'
                '<strong>SB_USER</strong> — No DARE access + SocraticBooks student/consumer only.'
            )
        }),
        (_('Model Access'), {
            'fields': ('model_group',),
            'description': 'Optional: link this access code group to a model group to restrict available LLMs.'
        }),
        (_('Wallet & Credits'), {
            'fields': ('initial_wallet_credit',),
            'description': 'If set, new users who register with this access code receive this starting wallet balance (credited above the default if necessary).'
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

    def expiration_status(self, obj):
        """Display expiration status with color coding"""
        if not obj.expires_at:
            return render_span("No Expiration", color="gray", italic=True)

        if obj.is_expired:
            return render_status_badge("EXPIRED", color="red", emoji="⚠️")

        days_until_expiration = (obj.expires_at - timezone.now()).days

        if days_until_expiration <= 7:
            return render_status_badge(
                f"{days_until_expiration}d left",
                color="orange",
                emoji="⏰"
            )
        elif days_until_expiration <= 30:
            return render_status_badge(
                f"{days_until_expiration}d left",
                color="yellow",
                emoji="📅"
            )
        else:
            return render_span(
                obj.expires_at.strftime('%Y-%m-%d'),
                color="green"
            )
    expiration_status.short_description = "Expiration"
    expiration_status.admin_order_field = "expires_at"

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
    
    actions = ["credit_groups_users"]


class UserAdmin(DjangoUserAdmin):
    class CreditActionForm(ActionForm):
        amount = forms.DecimalField(
            required=True,
            min_value=Decimal('0.01'),
            max_digits=10,
            decimal_places=6,
            help_text="Amount to credit to each selected user's wallet (USD)"
        )
        note = forms.CharField(required=False, max_length=255)

    action_form = CreditActionForm

    @admin.action(description="Credit selected users' wallets")
    def credit_selected_users(self, request, queryset):
        try:
            amount_str = request.POST.get('amount')
            note = request.POST.get('note') or 'Admin bulk credit'
            amount = Decimal(amount_str)
        except Exception:
            self.message_user(request, "Please provide a valid amount for crediting.", level=messages.ERROR)
            return

        count = 0
        for user in queryset:
            try:
                WalletService.add_topup(user, amount=amount, message=note)
                count += 1
            except Exception:
                continue

        self.message_user(request, f"Credited {count} user(s) with ${amount}.", level=messages.SUCCESS)

    def onboarding_status(self, obj):
        return "✓" if obj.is_onboarding_completed else "✗"
    onboarding_status.short_description = "Onboarded"


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
        (_("Onboarding Information"), {
              "fields": ("role", "industry", "purpose", "referral_source", "is_onboarding_completed"),
              "classes": ("collapse",)
        }),
        (_("Access Control"), {"fields": ("access_code_group",)}),
        (_("Platform Role"), {
            "fields": ("platform_role",),
            "description": (
                "User's role determines permissions across DARE and SocraticBots platforms.<br>"
                "<strong>SUPERADMIN</strong> — Full DARE + SB creator + admin privileges. "
                "<strong>SUPERVISOR</strong> — DARE access + cross-user bot/agent management in SB + creator access. "
                "<strong>RESEARCHER</strong> — DARE access + SB creator. "
                "<strong>USER</strong> — DARE access + SB student/consumer. "
                "<strong>CREATOR</strong> — No DARE + SB creator. "
                "<strong>SB_USER</strong> — No DARE + SB student/consumer only."
            )
        }),
        (_("Vector Database Settings"), {"fields": ("vector_db",)}),
        (_("Storage Settings"), {"fields": ("storage_backend",)}),
        (
            _("Syftbox"),
            {
                "fields": ("syftbox_access_token", "syftbox_refresh_token"),
                "classes": ("collapse",),
                "description": _("OAuth tokens for Syftbox storage (shown read-only)."),
            },
        ),
        (_("Platform Settings (Legacy)"), {
            "fields": ("auth_source", "is_dare_accessible", "is_socratic_bots_accessible"),
            "classes": ("collapse",),
            "description": "Legacy fields - platform access is now determined by platform_role"
        }),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "platform_role", "vector_db", "storage_backend", "auth_source", "is_superuser", "is_staff", "is_active"),
            },
        ),
    )
    list_display = ("email", "last_login_display", "date_joined", "activity_status", "is_active", "is_staff", "platform_role", "onboarding_status", "access_code_group", "vector_db", "storage_backend")
    list_filter = ("is_staff", "is_superuser", "is_active", "platform_role", LastLoginFilter, "vector_db", "storage_backend", "access_code_group", "auth_source")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-last_login",)
    actions = ["credit_selected_users", "disable_inactive_accounts"]
    date_hierarchy = "date_joined"

    def last_login_display(self, obj):
        if not obj.last_login:
            return render_span("Never", color="gray", italic=True)

        diff_days = (timezone.now() - obj.last_login).days
        color, template = next(
            (
                (color, template)
                for threshold, color, template in LAST_LOGIN_DISPLAY_RULES
                if diff_days <= threshold
            ),
            LAST_LOGIN_DISPLAY_DEFAULT,
        )
        status = template.format(days=diff_days)

        return render_span(
            status,
            color=color,
            title=obj.last_login.strftime('%Y-%m-%d %H:%M'),
        )
    last_login_display.short_description = "Last Login"
    last_login_display.admin_order_field = "last_login"

    def activity_status(self, obj):
        if not obj.last_login:
            state = ACTIVITY_NEVER_STATE
        else:
            days_since_login = (timezone.now() - obj.last_login).days
            state = next(
                (
                    level
                    for level in ACTIVITY_LEVELS
                    if days_since_login <= level["days"]
                ),
                ACTIVITY_LEVELS[-1],
            )

        return render_status_badge(
            state["label"],
            color=state["color"],
            emoji=state["emoji"],
        )
    activity_status.short_description = "Activity"
    activity_status.admin_order_field = "last_login"

    @admin.action(description="Disable selected inactive accounts")
    def disable_inactive_accounts(self, request, queryset):
        """Disable user accounts that haven't been active"""
        count = queryset.filter(is_active=True).update(is_active=False)
        self.message_user(request, f"Disabled {count} user account(s).", level=messages.SUCCESS)

    def get_readonly_fields(self, request, obj=None):
        return (
            *super().get_readonly_fields(request, obj),
            "syftbox_access_token",
            "syftbox_refresh_token",
        )


admin.site.register(User, UserAdmin)
