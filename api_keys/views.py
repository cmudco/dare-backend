"""
Views for API Keys app
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from api_keys.models import UserProviderAPIKey
from api_keys.serializers import (
    UserProviderAPIKeySerializer,
    UserProviderAPIKeyUpdateSerializer,
    BillingModeSerializer
)


class UserProviderAPIKeyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for managing user's provider API keys.

    Features:
    - List all API keys for the authenticated user (GET /api/user-api-keys/)
    - Get status of a specific provider key (GET /api/user-api-keys/{provider}/)
    - Update API key for a provider (POST /api/user-api-keys/update/)
    - Delete API key for a provider (DELETE /api/user-api-keys/{provider}/)
    - Get status of all providers (GET /api/user-api-keys/status/)
    - Update billing mode (POST /api/user-api-keys/billing-mode/)

    SECURITY:
    - Only shows keys for the authenticated user
    - Never exposes actual API keys in responses
    - Only returns masked keys for display
    """
    serializer_class = UserProviderAPIKeySerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'provider'

    def get_queryset(self):
        """Return only API keys for the authenticated user"""
        return UserProviderAPIKey.active_objects.filter(
            user=self.request.user
        ).select_related('user')

    @action(detail=False, methods=['post'], url_path='update')
    def update_key(self, request):
        """
        Create or update an API key for a specific provider.

        Request body:
        {
            "provider": "openai",
            "api_key": "sk-..."
        }

        The API key will be validated with the provider before saving.
        If validation fails, an error response will be returned.

        Returns the updated key information (with masked key) on success.
        """
        serializer = UserProviderAPIKeyUpdateSerializer(
            data=request.data,
            context={'request': request}
        )

        serializer.is_valid(raise_exception=True)

        user_provider_key = serializer.save()

        validation_result = serializer.context.get('validation_result')

        response_serializer = UserProviderAPIKeySerializer(user_provider_key)
        response_data = response_serializer.data

        if validation_result:
            response_data['validation'] = {
                'validated': True,
                'message': validation_result.message,
                'details': validation_result.details
            }

        return Response(response_data, status=status.HTTP_200_OK)

    def destroy(self, request, provider=None):
        """
        Delete (clear) the API key for a specific provider.

        URL: DELETE /api/user-api-keys/{provider}/

        Sets the api_key to null rather than deleting the record.
        """
        user_provider_key = get_object_or_404(
            UserProviderAPIKey,
            user=request.user,
            provider=provider
        )

        # Clear the API key
        user_provider_key.api_key = None
        user_provider_key.save(update_fields=['api_key', 'updated_at'])

        return Response(
            {'message': f'API key for {provider} has been cleared'},
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['get'], url_path='status')
    def provider_status(self, request):
        """
        Get status of all providers (which ones have keys set).

        Returns:
        {
            "openai": {"has_key": true, "masked_key": "sk-***xyz"},
            "claude": {"has_key": false, "masked_key": null},
            ...
        }
        """
        user_keys = self.get_queryset()
        status_dict = {}

        for key in user_keys:
            status_dict[key.provider] = {
                'has_key': key.has_key,
                'masked_key': key.get_masked_key(),
                'provider_display': key.get_provider_display()
            }

        return Response(status_dict, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get', 'patch'], url_path='billing-mode')
    def billing_mode(self, request):
        """
        Get or update the user's billing mode.

        GET: Returns current billing mode
        PATCH: Updates billing mode

        Request body for PATCH:
        {
            "billing_mode": "own_api"  // or "wallet"
        }
        """
        if request.method == 'GET':
            return Response({
                'billing_mode': request.user.billing_mode,
                'billing_mode_display': request.user.get_billing_mode_display()
            }, status=status.HTTP_200_OK)

        elif request.method == 'PATCH':
            serializer = BillingModeSerializer(
                instance=request.user,
                data=request.data,
                context={'request': request}
            )
            serializer.is_valid(raise_exception=True)
            user = serializer.save()

            return Response({
                'billing_mode': user.billing_mode,
                'billing_mode_display': user.get_billing_mode_display(),
                'message': 'Billing mode updated successfully'
            }, status=status.HTTP_200_OK)
