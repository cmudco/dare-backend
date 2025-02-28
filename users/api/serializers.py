from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):

    class Meta:
        model = User
        fields = [
            "id",
            "email",
        ]
        read_only_fields = ["id"]

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Combine first_name and last_name to form the full name
        full_name = f"{instance.first_name} {instance.last_name}".strip()
        representation["name"] = full_name
        return representation


class CustomRegisterSerializer(RegisterSerializer):
    name = serializers.CharField(max_length=255, required=True)

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
        return data

    def save(self, request):
        user = super().save(request)
        # Parse the name into first_name and last_name
        full_name = self.validated_data.get("name", "")
        name_parts = full_name.split(maxsplit=1)
        user.first_name = name_parts[0]
        user.last_name = name_parts[1] if len(name_parts) > 1 else ""

        user.save()
        return user
