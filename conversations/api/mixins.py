"""
Mixins for conversation-related viewsets.

Provides reusable functionality for sharing operations with consistent error handling.
"""
from typing import Callable
from rest_framework.response import Response
from rest_framework import status

from conversations.exceptions import SharingAPIException
from conversations.services.sharing_service import SharingValidationError


class ConversationSharingMixin:
    """
    Mixin for conversation sharing-related actions.

    Provides a generic handler for sharing operations (publish, fork, etc.)
    with consistent error handling and response serialization.

    The mixin expects the viewset to provide:
    - get_serializer(instance): Method to get the appropriate serializer
    """

    def handle_sharing_operation(
        self,
        operation_fn: Callable,
        success_status: int = status.HTTP_200_OK
    ) -> Response:
        """
        Generic handler for sharing operations with consistent error handling.

        Wraps a sharing service operation, automatically handling serialization
        and error conversion to DRF exceptions.

        Args:
            operation_fn: Callable that performs the sharing operation and returns
                         a model instance (Conversation, Message, etc.)
            success_status: HTTP status code for successful operations (default: 200)

        Returns:
            Response object with serialized data or error

        Raises:
            SharingAPIException: For sharing validation errors (automatically handled by DRF)

        Example:
            return self.handle_sharing_operation(
                lambda: ConversationSharingService.toggle_publish(
                    self.get_object(),
                    self.request.user
                ),
                success_status=status.HTTP_200_OK
            )
        """
        try:
            result = operation_fn()
            serializer = self.get_serializer(result)
            return Response(serializer.data, status=success_status)

        except SharingValidationError as e:
            # Convert to DRF exception for automatic handling
            raise SharingAPIException(e)
