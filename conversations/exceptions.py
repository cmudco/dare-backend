"""
REST Framework-compatible exceptions for conversations app.

These exceptions integrate with DRF's exception handling system
to provide consistent error responses across all conversation endpoints.
"""
from rest_framework.exceptions import APIException
from rest_framework import status

from conversations.constants import SharingErrorCode
from conversations.services.sharing_service import SharingValidationError


class SharingAPIException(APIException):
    """
    DRF-compatible exception wrapper for SharingValidationError.

    Automatically maps internal sharing error codes to appropriate HTTP status codes
    and formats error responses consistently.

    Usage:
        raise SharingAPIException(sharing_validation_error)
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'sharing_error'

    # Map internal error codes to HTTP status codes
    STATUS_CODE_MAP = {
        SharingErrorCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
        SharingErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
        SharingErrorCode.CANNOT_PUBLISH_FORKED: status.HTTP_400_BAD_REQUEST,
    }

    def __init__(self, sharing_error: SharingValidationError):
        """
        Initialize with a SharingValidationError.

        Args:
            sharing_error: The internal sharing validation error to wrap
        """
        self.detail = {
            "error": str(sharing_error),
            "code": sharing_error.error_code
        }

        # Set appropriate HTTP status code
        self.status_code = self.STATUS_CODE_MAP.get(
            sharing_error.error_code,
            status.HTTP_400_BAD_REQUEST
        )
