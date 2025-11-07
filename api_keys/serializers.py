"""
Serializers for API Keys app
"""
from rest_framework import serializers
from api_keys.models import UserProviderAPIKey
from api_keys.constants import BillingModeChoice
from api_keys.services import APIKeyValidationService


class UserProviderAPIKeySerializer(serializers.ModelSerializer):
    """
    Serializer for UserProviderAPIKey model.

    SECURITY: Never expose the actual API key in responses.
    Only return masked_key for display purposes.
    """
    masked_key = serializers.SerializerMethodField()
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)

    class Meta:
        model = UserProviderAPIKey
        fields = [
            'id',
            'provider',
            'provider_display',
            'has_key',
            'masked_key',
            'is_active',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'has_key', 'masked_key', 'provider_display', 'created_at', 'updated_at']

    def get_masked_key(self, obj):
        """Return masked version of API key for security"""
        return obj.get_masked_key()


class UserProviderAPIKeyUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating a user's API key for a specific provider.

    Only accepts the provider and api_key fields.
    The user is determined from the request context.

    Validates the API key by making a test request to the provider's API.
    """
    provider = serializers.ChoiceField(
        choices=['openai', 'claude', 'gemini', 'llama'],
        required=True,
        help_text="Provider to update the API key for"
    )
    api_key = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=500,
        help_text="API key for the provider"
    )

    def validate_api_key(self, value):
        """Validate that API key is not empty"""
        if not value or not value.strip():
            raise serializers.ValidationError("API key cannot be empty")
        return value.strip()

    def validate(self, attrs):
        """
        Validate the API key by making a test request to the provider.

        This ensures the key is valid before saving it to the database.
        """
        provider = attrs.get('provider')
        api_key = attrs.get('api_key')

        validation_result = APIKeyValidationService.validate_api_key(provider, api_key)

        if not validation_result.is_valid:
            raise serializers.ValidationError({
                'api_key': validation_result.user_friendly_message
            })

        self.context['validation_result'] = validation_result

        return attrs

    def create(self, validated_data):
        """
        Create or update the API key for the user and provider.

        The user is taken from the request context.
        The API key has already been validated at this point.
        """
        user = self.context['request'].user
        provider = validated_data['provider']
        api_key = validated_data['api_key']

        # Get or create the UserProviderAPIKey record
        user_provider_key, created = UserProviderAPIKey.active_objects.get_or_create(
            user=user,
            provider=provider,
            defaults={'api_key': api_key, 'is_active': True}
        )

        # If it already exists, update the API key
        if not created:
            user_provider_key.api_key = api_key
            user_provider_key.is_active = True
            user_provider_key.save(update_fields=['api_key', 'is_active', 'updated_at'])

        return user_provider_key


class BillingModeSerializer(serializers.Serializer):
    """
    Serializer for updating user's billing mode.
    """
    billing_mode = serializers.ChoiceField(
        choices=BillingModeChoice.choices,
        required=True,
        help_text="Billing mode: 'wallet' or 'own_api'"
    )

    def validate_billing_mode(self, value):
        """
        Validate that if switching to OWN_API mode, user has at least one API key set.
        """
        user = self.context['request'].user

        if value == BillingModeChoice.OWN_API:
            # Check if user has at least one API key set
            has_any_key = UserProviderAPIKey.active_objects.filter(
                user=user,
                api_key__isnull=False
            ).exclude(api_key='').exists()

            if not has_any_key:
                raise serializers.ValidationError(
                    "You must set at least one API key before switching to 'Use Own API Keys' mode."
                )

        return value

    def update(self, instance, validated_data):
        """Update the user's billing mode"""
        instance.billing_mode = validated_data['billing_mode']
        instance.save(update_fields=['billing_mode'])
        return instance
