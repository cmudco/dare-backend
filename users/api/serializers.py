from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from django.contrib.auth import get_user_model
from rest_framework import serializers
from prompts.api.serializers import PromptSerializer
from users.constants import VectorDBChoice
from users.models import AccessCodeGroup

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):
    vector_db = serializers.ChoiceField(choices=VectorDBChoice.choices)
    default_prompt = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "vector_db",
            "default_prompt"
        ]
        read_only_fields = ["id"]

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
    access_code = serializers.CharField(max_length=255, required=True)

    def validate_access_code(self, access_code):
        """
        Validate that the access code exists and is available for use.
        """
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
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )
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

        access_code = self.validated_data.get("access_code")
        if access_code:
            try:
                code_group = AccessCodeGroup.objects.get(access_code=access_code)
                code_group.use_code()
            except AccessCodeGroup.DoesNotExist:
                pass

        user.save()
        return user
