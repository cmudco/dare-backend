import logging
from decimal import Decimal

from allauth.account.models import EmailAddress
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import LoginSerializer, UserDetailsSerializer
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from billing.constants import TransactionSourceChoice
from billing.group_wallet import adopt_group_wallet
from billing.models import Wallet
from billing.services import WalletService
from core.storage.constants import StorageBackendChoice
from prompts.api.serializers import PromptSerializer
from users.access_request_constants import RequestKind, RequestState
from users.access_request_models import AccessRequest
from users.constants import AuthSourceChoice, RoleChoice, VectorDBChoice
from users.models import AccessCodeGroup
from users.services.access_request_service import (
    AccessRequestsUnavailable,
    active_policy,
    request_registration_approval,
)
from users.utils import detect_platform_from_request, get_platform_access_permission

logger = logging.getLogger(__name__)

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):
    vector_db = serializers.ChoiceField(choices=VectorDBChoice.choices)
    default_prompt = serializers.SerializerMethodField()
    model_group = serializers.SerializerMethodField()
    auth_source = serializers.ChoiceField(
        choices=AuthSourceChoice.choices, read_only=True
    )
    platform_role = serializers.ChoiceField(choices=RoleChoice.choices, read_only=True)
    billing_mode = serializers.CharField(read_only=True)
    billing_mode_display = serializers.CharField(
        source="get_billing_mode_display", read_only=True
    )
    avatar_url = serializers.SerializerMethodField()
    is_syftbox_file_storage = serializers.SerializerMethodField()
    access_code_group = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "is_active",
            "vector_db",
            "default_prompt",
            "model_group",
            "role",
            "industry",
            "purpose",
            "referral_source",
            "is_onboarding_completed",
            "auth_source",
            "platform_role",
            "billing_mode",
            "billing_mode_display",
            "avatar_type",
            "avatar_preset",
            "avatar_url",
            "is_syftbox_file_storage",
            "access_code_group",
        ]
        read_only_fields = [
            "id",
            "auth_source",
            "platform_role",
            "billing_mode",
            "billing_mode_display",
            "is_onboarding_completed",
        ]

    def get_default_prompt(self, obj):
        if obj.default_prompt:
            return PromptSerializer(obj.default_prompt).data
        return None

    def get_model_group(self, obj):
        # Only use AccessCodeGroup -> ModelGroup mapping
        acg = getattr(obj, "access_code_group", None)
        group = acg.model_group if acg and getattr(acg, "model_group", None) else None

        if group:
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "isActive": group.is_active,
            }
        return None

    def get_is_syftbox_file_storage(self, obj):
        return obj.storage_backend == StorageBackendChoice.SYFTBOX

    def get_avatar_url(self, obj):
        """Return absolute URL for avatar, or None if not set."""
        if not obj.avatar_url:
            return None

        # If already an absolute URL, return as-is
        if obj.avatar_url.startswith("http://") or obj.avatar_url.startswith(
            "https://"
        ):
            return obj.avatar_url

        # Build absolute URL from request context
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.avatar_url)

        # Fallback: return the relative URL
        return obj.avatar_url

    def get_access_code_group(self, obj):
        acg = getattr(obj, "access_code_group", None)
        if not acg:
            return None
        return {
            "id": acg.id,
            "accessCode": acg.access_code,
            "memberCount": acg.users.filter(is_active=True).count(),
        }

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        full_name = f"{instance.first_name} {instance.last_name}".strip()
        representation["name"] = full_name
        return representation

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)

        # Only check if any onboarding field was updated
        onboarding_fields = {"role", "industry", "purpose", "referral_source"}
        if onboarding_fields.intersection(validated_data.keys()):
            # Auto-set if all fields are now complete
            if (
                instance.role
                and instance.industry
                and instance.purpose
                and instance.referral_source
            ):
                instance.is_onboarding_completed = True
                instance.save(update_fields=["is_onboarding_completed"])
        return instance


class CustomRegisterSerializer(RegisterSerializer):
    name = serializers.CharField(max_length=255, required=True)
    access_code = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("access_code"):
            try:
                active_policy()
            except AccessRequestsUnavailable:
                raise serializers.ValidationError(
                    {
                        "access_code": _(
                            "Registration without a code is unavailable. Use a valid invitation code or contact the deployment administrator."
                        )
                    }
                )
            existing = AccessRequest.objects.filter(email=attrs["email"]).first()
            if existing and existing.status != RequestState.PENDING:
                if (
                    existing.kind == RequestKind.INVITATION
                    and existing.status == RequestState.APPROVED
                ):
                    raise serializers.ValidationError(
                        {
                            "access_code": _(
                                "Your invitation is already approved. Enter the single-use code emailed to this address in the Access Code field. If the invitation email is missing, ask the administrator to retry its delivery."
                            )
                        }
                    )
                raise serializers.ValidationError(
                    {
                        "email": _(
                            "An access request already exists. Use your invitation or contact the deployment administrator."
                        )
                    }
                )
            return attrs
        try:
            group = AccessCodeGroup.objects.get(access_code=attrs["access_code"])
        except AccessCodeGroup.DoesNotExist:
            raise serializers.ValidationError(
                {"access_code": _("Invalid access code.")}
            )
        if not group.is_available:
            raise serializers.ValidationError(
                {
                    "access_code": _(
                        "This access code is expired, disabled, or fully used."
                    )
                }
            )
        self.validate_invitation(group, attrs["email"])
        return attrs

    @staticmethod
    def validate_invitation(group, email):
        invitation = AccessRequest.objects.filter(code_group=group).first()
        if invitation and invitation.email != email:
            raise serializers.ValidationError(
                {
                    "access_code": _(
                        "This invitation is not valid for this email address."
                    )
                }
            )
        return invitation

    def validate_email(self, email):
        """
        Check if a user with this email already exists.
        """
        email = self.normalize_email(email)
        try:
            existing_user = User.objects.get(email__iexact=email)
            if not existing_user.is_active:
                raise serializers.ValidationError(
                    "An account with this email address exists but is currently inactive. "
                    "Please contact the administrator for assistance."
                )
            else:
                raise serializers.ValidationError(
                    "A user with this email address already exists."
                )
        except User.DoesNotExist:
            pass
        return email

    def normalize_email(self, email):
        """Normalize the email address"""
        return email and email.lower() or ""

    def get_cleaned_data(self):
        data = super().get_cleaned_data()
        data["name"] = self.validated_data.get("name", "")
        data["access_code"] = self.validated_data.get("access_code", "")
        return data

    @transaction.atomic
    def save(self, request):
        code_group = None
        code = self.validated_data.get("access_code")
        if code:
            try:
                code_group = AccessCodeGroup.objects.select_for_update().get(
                    access_code=code
                )
            except AccessCodeGroup.DoesNotExist:
                raise serializers.ValidationError(
                    {"access_code": _("This access code is no longer available.")}
                )
            invitation = self.validate_invitation(
                code_group, self.validated_data["email"]
            )
            if not code_group.use_code():
                raise serializers.ValidationError(
                    {"access_code": _("This access code is no longer available.")}
                )
            if invitation:
                # Generated invitations expire before redemption, not the account
                # they activate. Legacy groups retain their existing expiry policy.
                code_group.expires_at = None
                code_group.save(update_fields=["expires_at"])
        try:
            # Validation can race another signup for the same address. A savepoint
            # keeps a uniqueness failure from breaking the surrounding transaction.
            with transaction.atomic():
                user = super().save(request)
        except IntegrityError:
            if User.objects.filter(email__iexact=self.validated_data["email"]).exists():
                raise serializers.ValidationError(
                    {"email": _("An account with this email address already exists.")}
                )
            raise
        full_name = self.validated_data.get("name", "")
        name_parts = full_name.split(maxsplit=1)
        user.first_name = name_parts[0]
        user.last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Set auth_source based on platform detection
        platform = detect_platform_from_request(request)
        user.auth_source = platform

        user.access_code_group = code_group
        user.platform_role = code_group.default_role if code_group else RoleChoice.USER
        if not code_group:
            user.is_active = False

        user.save()
        if not code_group:
            try:
                request_registration_approval(user, full_name)
            except (AccessRequestsUnavailable, DjangoValidationError):
                raise serializers.ValidationError(
                    {
                        "email": _(
                            "Could not queue administrator review. Use your invitation or contact the deployment administrator."
                        )
                    }
                )

        # Members of a group that was issued a LiteLLM key route through it by
        # default. Their own wallet and credit below are untouched — this sets
        # the default provider, not a restriction.
        adopt_group_wallet(user)

        # Apply initial wallet credit with a single transaction
        # - If access code has an initial_wallet_credit, grant exactly that amount
        # - Otherwise grant the default $5.00
        try:
            # Ensure wallet exists with $0 from signal; create with $0 if missing
            wallet = getattr(user, "wallet", None)
            if wallet is None:
                wallet = Wallet.objects.create(user=user, balance=Decimal("0.00"))

            if (
                code_group
                and getattr(code_group, "initial_wallet_credit", None) is not None
            ):
                initial_amount = Decimal(code_group.initial_wallet_credit)
                message = f"Initial credit via access code '{code_group.access_code}'"
            else:
                initial_amount = Decimal("5.00")
                message = "Initial wallet credit"

            # Credit exactly the intended amount (wallet starts at $0)
            if initial_amount > Decimal("0.00"):
                WalletService.add_topup(
                    user,
                    amount=initial_amount,
                    message=message,
                    source=TransactionSourceChoice.REGISTRATION,
                    related_group=code_group,
                )
        except Exception:
            # Do not block registration if crediting fails
            logger.exception(
                f"Failed to apply initial wallet credit for user {user.id}"
            )
            pass
        return user


class CustomLoginSerializer(LoginSerializer):
    def validate(self, attrs):
        email = attrs.get("email")

        if email:
            try:
                user = User.objects.get(email__iexact=email)

                # Check if user's access code has expired and deactivate if necessary
                if (
                    user.access_code_group
                    and user.access_code_group.is_expired
                    and user.is_active
                ):
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                    logger.warning(
                        f"User {user.email} deactivated due to expired access code {user.access_code_group.access_code}"
                    )

                if not user.is_active:
                    # Check if it's due to expired access code
                    if user.access_code_group and user.access_code_group.is_expired:
                        raise serializers.ValidationError(
                            "Your account has been deactivated because your access code has expired. Please contact the administrator for assistance."
                        )
                    if (
                        not user.is_deleted
                        and user.check_password(attrs.get("password", ""))
                        and (
                            not user.access_code_group
                            or user.access_code_group.is_active
                        )
                    ):
                        registration = AccessRequest.objects.filter(
                            registered_user=user,
                            kind=RequestKind.REGISTRATION,
                            activated_at__isnull=True,
                        ).first()
                        if registration:
                            verified = EmailAddress.objects.filter(
                                user=user, email__iexact=user.email, verified=True
                            ).exists()
                            if (
                                registration.status == RequestState.APPROVED
                                and not verified
                            ):
                                raise serializers.ValidationError(
                                    _(
                                        "Your signup is approved, but your e-mail is not verified. Follow the verification link in your email or use Resend Verification Email below before signing in."
                                    )
                                )
                            if registration.status == RequestState.PENDING:
                                raise serializers.ValidationError(
                                    _(
                                        "Your email is verified. Your signup is awaiting administrator approval before you can sign in."
                                    )
                                    if verified
                                    else _(
                                        "Your e-mail is not verified, and your signup is awaiting administrator approval. Follow the verification link in your email or use Resend Verification Email below."
                                    )
                                )
                    raise serializers.ValidationError(
                        "Your account is currently inactive. Please contact the administrator for assistance."
                    )

                request = self.context.get("request")
                if request:
                    platform = detect_platform_from_request(request)
                    if not get_platform_access_permission(user, platform):
                        platform_name = (
                            "DARE"
                            if platform == AuthSourceChoice.DARE
                            else "SocraticBots"
                        )
                        raise serializers.ValidationError(
                            f"You do not have access to the {platform_name} platform. Please contact the administrator for assistance."
                        )

            except User.DoesNotExist:
                pass

        return super().validate(attrs)
