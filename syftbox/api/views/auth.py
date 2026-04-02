from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core.storage.constants import StorageBackendChoice
from syftbox.api.serializers.auth import VerifyOtpSerializer
from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.syftbox_auth_service import SyftBoxAuthService


class SyftboxAuthView(ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="request-otp")
    def request_otp(self, request):
        email = request.user.email
        auth_service = SyftBoxAuthService()

        try:
            payload = auth_service.request_otp(email=email)
            return Response(
                {
                    "detail": _("OTP requested successfully"),
                    "email": email,
                    "syftbox_response": payload,
                },
                status=status.HTTP_200_OK,
            )
        except SyftBoxException as error:
            http_status = (
                status.HTTP_400_BAD_REQUEST
                if error.code in {SyftBoxErrorCode.OTP_REQUIRED, SyftBoxErrorCode.INVALID_REQUEST}
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response(
                {
                    "error": str(error.message),
                    "code": error.code.value,
                    "details": error.details,
                },
                status=http_status,
            )

    @action(detail=False, methods=["post"], url_path="verify-otp")
    def verify_otp(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = request.user.email
        code = serializer.validated_data["code"]
        auth_service = SyftBoxAuthService()

        try:
            tokens = auth_service.verify_otp(email=email, code=code)
            request.user.syftbox_access_token = tokens.access_token
            request.user.syftbox_refresh_token = tokens.refresh_token
            request.user.storage_backend = StorageBackendChoice.SYFTBOX
            request.user.save(
                update_fields=[
                    "syftbox_access_token",
                    "syftbox_refresh_token",
                    "storage_backend",
                ]
            )
            return Response(
                {
                    "detail": _("OTP verified successfully"),
                    "storage_backend": request.user.storage_backend,
                },
                status=status.HTTP_200_OK,
            )
        except SyftBoxException as error:
            http_status = (
                status.HTTP_400_BAD_REQUEST
                if error.code
                in {
                    SyftBoxErrorCode.OTP_INVALID,
                    SyftBoxErrorCode.OTP_EXPIRED,
                    SyftBoxErrorCode.INVALID_REQUEST,
                }
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response(
                {
                    "error": str(error.message),
                    "code": error.code.value,
                    "details": error.details,
                },
                status=http_status,
            )
