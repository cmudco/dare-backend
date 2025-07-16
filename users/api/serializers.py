from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer, LoginSerializer
from django.contrib.auth import get_user_model, authenticate
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice, AuthSourceChoice
from users.models import AccessCodeGroup
from users.utils import detect_platform_from_request, get_platform_access_permission

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):
    vector_db = serializers.ChoiceField(choices=VectorDBChoice.choices)
    default_prompt = serializers.SerializerMethodField()
    auth_source = serializers.ChoiceField(choices=AuthSourceChoice.choices, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "is_active",
            "vector_db",
            "default_prompt",
            "auth_source",
            "is_dare_accessible",
            "is_socratic_books_accessible"
        ]
        read_only_fields = ["id", "auth_source"]

    def get_default_prompt(self, obj):
        if obj.default_prompt:
            return PromptSerializer(obj.default_prompt).data
        return None

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        full_name = f"{instance.first_name} {instance.last_name}".strip()
        representation["name"] = full_name
        return representation


class CustomRegisterSerializer(RegisterSerializer):
    name = serializers.CharField(max_length=255, required=True)
    access_code = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_access_code(self, access_code):
        """
        Validate access code based on platform:
        - DARE: access_code is required
        - SocraticBooks: access_code is optional
        """
        request = self.context.get('request')
        platform = detect_platform_from_request(request) if request else AuthSourceChoice.DARE

        # If no access code provided
        if not access_code:
            if platform == AuthSourceChoice.DARE:
                raise serializers.ValidationError(
                    "Access code is required for DARE registration."
                )
            # For SocraticBooks, empty access code is allowed
            return access_code

        # If access_code is provided, validate it exists and is available
        try:
            code_group = AccessCodeGroup.objects.get(access_code=access_code)
            if not code_group.is_available:
                if not code_group.is_active:
                    raise serializers.ValidationError(
                        "This access code is no longer active."
                    )
                else:
                    raise serializers.ValidationError(
                        "This access code has reached its maximum usage limit."
                    )
            return access_code
        except AccessCodeGroup.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid access code. Please check your code and try again."
            )

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

        # Set platform accessibility based on auth_source
        if platform == AuthSourceChoice.DARE:
            user.is_dare_accessible = True
            user.is_socratic_books_accessible = False
        elif platform == AuthSourceChoice.SOCRATIC_BOOKS:
            user.is_dare_accessible = False
            user.is_socratic_books_accessible = True

        access_code = self.validated_data.get("access_code")
        if access_code:
            try:
                code_group = AccessCodeGroup.objects.get(access_code=access_code)
                code_group.use_code()
                user.access_code_group = code_group
            except AccessCodeGroup.DoesNotExist:
                pass

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
                        platform_name = "DARE" if platform == AuthSourceChoice.DARE else "SocraticBooks"
                        raise serializers.ValidationError(
                            f"You do not have access to the {platform_name} platform. Please contact the administrator for assistance."
                        )

            except User.DoesNotExist:
                pass

        return super().validate(attrs)
