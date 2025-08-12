from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer, LoginSerializer
from django.contrib.auth import get_user_model, authenticate
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice, AuthSourceChoice, ScopeChoice
from users.models import AccessCodeGroup
from users.utils import detect_platform_from_request, get_platform_access_permission
import logging

logger = logging.getLogger(__name__)

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):
    vector_db = serializers.ChoiceField(choices=VectorDBChoice.choices)
    default_prompt = serializers.SerializerMethodField()
    model_group = serializers.SerializerMethodField()
    auth_source = serializers.ChoiceField(choices=AuthSourceChoice.choices, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "is_active",
            "vector_db",
            "default_prompt",
            "model_group",
            "auth_source",
            "is_dare_accessible",
            "is_socratic_bots_accessible"
        ]
        read_only_fields = ["id", "auth_source"]

    def get_default_prompt(self, obj):
        if obj.default_prompt:
            return PromptSerializer(obj.default_prompt).data
        return None

    def get_model_group(self, obj):
        # Only use AccessCodeGroup -> ModelGroup mapping
        acg = getattr(obj, 'access_code_group', None)
        group = acg.model_group if acg and getattr(acg, 'model_group', None) else None

        if group:
            return {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "isActive": group.is_active
            }
        return None

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        full_name = f"{instance.first_name} {instance.last_name}".strip()
        representation["name"] = full_name
        return representation


class CustomRegisterSerializer(RegisterSerializer):
    name = serializers.CharField(max_length=255, required=True)
    access_code = serializers.CharField(max_length=255, required=False, allow_blank=True)

    validate_access_code_attrs = {
        # Platform rules:
        # - DARE: access_code is optional
        # - SocraticBots: access_code is optional
        AuthSourceChoice.DARE: {'access_code_required': False},
        AuthSourceChoice.SOCRATIC_BOTS: {'access_code_required': False}
    }

    def validate(self, attrs):
        """
        Validate registration based on platform detection
        """
        # Get platform from request
        platform = detect_platform_from_request(self.context['request'])
        
        access_code = attrs.get("access_code")
        validation_rules = self.validate_access_code_attrs.get(platform, {})
        
        # For SocraticBots, empty access code is allowed
        if platform == AuthSourceChoice.SOCRATIC_BOTS and not access_code:
            return attrs

        # If access code is provided, validate it exists and is active
        if access_code:
            try:
                code_group = AccessCodeGroup.objects.get(access_code=access_code)
                if not code_group.is_available:
                    raise serializers.ValidationError({
                        "access_code": "This access code has reached its usage limit or is no longer active."
                    })
                attrs['_code_group'] = code_group
            except AccessCodeGroup.DoesNotExist:
                raise serializers.ValidationError({
                    "access_code": "Invalid access code. Please check the code and try again."
                })
        
        # Check if access code is required for the platform
        elif validation_rules.get('access_code_required', False):
            raise serializers.ValidationError({
                "access_code": f"Access code is required for {platform} registration."
            })
            
        return attrs

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

    def save(self, request):
        user = super().save(request)
        full_name = self.validated_data.get("name", "")
        name_parts = full_name.split(maxsplit=1)
        user.first_name = name_parts[0]
        user.last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Set auth_source based on platform detection
        platform = detect_platform_from_request(request)
        user.auth_source = platform

        access_code = self.validated_data.get("access_code")
        code_group = None
        
        if access_code:
            try:
                code_group = AccessCodeGroup.objects.get(access_code=access_code)
                code_group.use_code()
                user.access_code_group = code_group
            except AccessCodeGroup.DoesNotExist:
                pass

        # Set platform accessibility based on auth_source and access code scope
        if platform == AuthSourceChoice.DARE:
            user.is_dare_accessible = True
            # If access code has DUAL scope, also give SocraticBots access
            if code_group and code_group.scope == ScopeChoice.DUAL:
                user.is_socratic_bots_accessible = True
            else:
                user.is_socratic_bots_accessible = False
                
        elif platform == AuthSourceChoice.SOCRATIC_BOTS:
            user.is_socratic_bots_accessible = True
            # If access code has DUAL scope, also give DARE access
            if code_group and code_group.scope == ScopeChoice.DUAL:
                user.is_dare_accessible = True
            else:
                user.is_dare_accessible = False
        
        user.save()
        return user


class CustomLoginSerializer(LoginSerializer):
    def validate(self, attrs):
        email = attrs.get('email')

        if email:
            try:
                user = User.objects.get(email__iexact=email)

                if not user.is_active:
                    raise serializers.ValidationError(
                        "Your account is currently inactive. Please contact the administrator for assistance."
                    )

                request = self.context.get('request')
                if request:
                    platform = detect_platform_from_request(request)
                    if not get_platform_access_permission(user, platform):
                        platform_name = "DARE" if platform == AuthSourceChoice.DARE else "SocraticBots"
                        raise serializers.ValidationError(
                            f"You do not have access to the {platform_name} platform. Please contact the administrator for assistance."
                        )

            except User.DoesNotExist:
                pass

        return super().validate(attrs)
