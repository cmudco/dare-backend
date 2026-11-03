from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from users.access_request_constants import ACCESS_REQUEST_THROTTLE_RATE
from users.services.access_request_service import (
    AccessRequestsUnavailable,
    request_access,
)


class AccessRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField(max_length=254)
    reason = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, default=""
    )

    def validate_email(self, value):
        return value.lower()


class AccessRequestResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class AccessRequestThrottle(SimpleRateThrottle):
    scope = "access_request"

    def get_rate(self):
        return getattr(
            settings, "ACCESS_REQUEST_THROTTLE_RATE", ACCESS_REQUEST_THROTTLE_RATE
        )

    def get_cache_key(self, request, view):
        # Forwarded headers are client input unless the deployment sanitizes them.
        ident = (
            self.get_ident(request)
            if api_settings.NUM_PROXIES
            else request.META.get("REMOTE_ADDR", "unknown")
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AccessRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AccessRequestThrottle]

    @extend_schema(
        request=AccessRequestSerializer,
        responses={
            202: AccessRequestResponseSerializer,
            503: AccessRequestResponseSerializer,
        },
        description="Request a signup invitation. Existing users and duplicate requests receive the same generic response. Requests do not create accounts or issue credentials.",
    )
    def post(self, request):
        serializer = AccessRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            request_access(**serializer.validated_data)
        except AccessRequestsUnavailable:
            return Response(
                AccessRequestResponseSerializer(
                    {
                        "detail": _(
                            "Access requests are currently unavailable. Contact your deployment administrator."
                        )
                    }
                ).data,
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            AccessRequestResponseSerializer(
                {
                    "detail": _(
                        "Your request has been received. If eligible, you will receive an invitation after administrator review. Contact your deployment administrator if you need assistance."
                    )
                }
            ).data,
            status=status.HTTP_202_ACCEPTED,
        )
