from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.contrib.admin.helpers import ActionForm

from users.models import User, AccessCodeGroup
from billing.services import WalletService
from django import forms
from decimal import Decimal
from users.constants import VectorDBChoice, AuthSourceChoice, ScopeChoice


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
    list_display = ('access_code', 'scope', 'model_group', 'initial_wallet_credit', 'usage_display', 'is_active', 'user_count', 'created_at')
    list_filter = ('is_active', 'scope', 'created_at', 'model_group')
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
            'fields': ('access_code', 'max_capacity', 'is_active')
        }),
        (_('Platform Access'), {
            'fields': ('scope',),
            'description': 'DUAL scope allows users to access both DARE and SocraticBots platforms'
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
            "fields": ("auth_source", "is_dare_accessible", "is_socratic_bots_accessible")
        }),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "vector_db", "auth_source", "is_dare_accessible", "is_socratic_bots_accessible", "is_superuser", "is_staff", "is_active"),
            },
        ),
    )
    list_display = ("email", "is_staff", "is_active", "is_superuser", "access_code_group", "vector_db", "auth_source", "is_dare_accessible", "is_socratic_bots_accessible")
    list_filter = ("is_staff", "is_superuser", "is_active", "vector_db", "access_code_group", "auth_source", "is_dare_accessible", "is_socratic_bots_accessible")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    actions = ["credit_selected_users"]


admin.site.register(User, UserAdmin)
