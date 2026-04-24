from rest_framework import serializers

from billing.constants import PolicySourceChoice
from billing.models import (
    GroupWallet,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    Wallet,
)
from billing.services import WalletService
from conversations.api.serializers import LLMSerializer
from users.models import AccessCodeGroup, User


class WalletSerializer(serializers.ModelSerializer):
    display_balance = serializers.CharField(read_only=True)

    class Meta:
        model = Wallet
        fields = ["display_balance", "last_refill_at", "created_at", "updated_at"]


class TransactionSerializer(serializers.ModelSerializer):
    display_amount = serializers.CharField(read_only=True)
    type = serializers.CharField(source="get_type_display")
    llm = LLMSerializer(read_only=True)
    billing_mode = serializers.CharField(source="get_billing_mode_display", read_only=True)
    platform = serializers.CharField(source="get_platform_display", read_only=True)
    source = serializers.CharField(read_only=True)
    related_group_code = serializers.CharField(source="related_group.access_code", read_only=True, default=None)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "display_amount",
            "type",
            "source",
            "related_group_code",
            "message",
            "llm",
            "llm_name",
            "input_tokens",
            "output_tokens",
            "billing_mode",
            "platform",
            "created_at",
            "updated_at",
        ]


# --- System refill policy -------------------------------------------------

class SystemRefillPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemRefillPolicy
        fields = ["refill_amount", "refill_period_days", "updated_at"]

    def validate_refill_amount(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError("Refill amount must be non-negative.")
        return value

    def validate_refill_period_days(self, value):
        if value is None or value < 1:
            raise serializers.ValidationError("Refill period must be at least 1 day.")
        return value


# --- Effective policy / overrides -----------------------------------------

class EffectivePolicySerializer(serializers.Serializer):
    """Flat, typed representation of a user's resolved refill policy."""
    amount = serializers.DecimalField(max_digits=10, decimal_places=6)
    period_days = serializers.IntegerField()
    amount_source = serializers.ChoiceField(choices=PolicySourceChoice.choices)
    period_source = serializers.ChoiceField(choices=PolicySourceChoice.choices)


class UserRefillOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRefillOverride
        fields = ["refill_amount", "refill_period_days", "reason", "updated_at"]


class UpsertUserOverrideSerializer(serializers.Serializer):
    """Write payload for creating or updating a user's refill override."""
    refill_amount = serializers.DecimalField(
        max_digits=10, decimal_places=6, required=False, allow_null=True,
    )
    refill_period_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    clear_amount = serializers.BooleanField(required=False, default=False)
    clear_period = serializers.BooleanField(required=False, default=False)


# --- Group wallet ---------------------------------------------------------

class GroupWalletReadSerializer(serializers.ModelSerializer):
    display_budget = serializers.CharField(read_only=True)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = GroupWallet
        fields = [
            "id",
            "budget_balance",
            "display_budget",
            "refill_amount",
            "refill_period_days",
            "is_active",
            "member_count",
            "created_at",
            "updated_at",
        ]

    def get_member_count(self, obj):
        return obj.group.users.count()


class GroupWalletWriteSerializer(serializers.Serializer):
    refill_amount = serializers.DecimalField(
        max_digits=10, decimal_places=6, required=False, allow_null=True,
    )
    refill_period_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    is_active = serializers.BooleanField(required=False)
    clear_amount = serializers.BooleanField(required=False, default=False)
    clear_period = serializers.BooleanField(required=False, default=False)


class FundBudgetSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=15, decimal_places=6, min_value=0.000001)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class AllocateSerializer(serializers.Serializer):
    recipient_user_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=6, min_value=0.000001)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class MemberRowSerializer(serializers.ModelSerializer):
    """Single member row with resolved effective policy + current override state."""
    display_balance = serializers.SerializerMethodField()
    effective_policy = serializers.SerializerMethodField()
    override = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "display_balance",
                  "effective_policy", "override"]

    def get_display_balance(self, obj):
        wallet = getattr(obj, "wallet", None)
        return wallet.display_balance if wallet else "$0.00"

    def get_effective_policy(self, obj):
        # EffectiveRefillPolicy is a frozen dataclass whose attribute names
        # match EffectivePolicySerializer fields — pass it through directly.
        policy = WalletService.get_effective_refill_policy(obj)
        return EffectivePolicySerializer(policy).data

    def get_override(self, obj):
        override = getattr(obj, "refill_override", None)
        if override is None:
            return None
        return UserRefillOverrideSerializer(override).data


class OwnedGroupSerializer(serializers.ModelSerializer):
    """Response shape for /group-wallets/owned/ — nests wallet + members."""
    group_wallet = GroupWalletReadSerializer(read_only=True)
    members = MemberRowSerializer(source="users", many=True, read_only=True)

    class Meta:
        model = AccessCodeGroup
        fields = [
            "id",
            "access_code",
            "notes",
            "is_active",
            "group_wallet",
            "members",
            "created_at",
            "updated_at",
        ]
