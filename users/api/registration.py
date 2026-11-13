"""Register code holders normally and keep code-free applicants inactive."""

from functools import partial

from allauth.account import app_settings as account_settings
from allauth.account import signals
from allauth.account.models import EmailAddress
from dj_rest_auth.registration.views import RegisterView
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.response import Response

from users.api.access_requests import (
    AccessRequestResponseSerializer,
    AccessRequestThrottle,
)
from users.constants import AuthSourceChoice
from users.services.access_request_service import (
    enqueue_signup_verification,
    registration_approval_enabled,
)
from users.utils import detect_platform_from_request


class RegistrationResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    approval_required = serializers.BooleanField()
    email_delivery_configured = serializers.BooleanField()


@extend_schema_view(
    post=extend_schema(
        responses={
            201: RegistrationResponseSerializer,
            503: AccessRequestResponseSerializer,
        }
    )
)
class CustomRegisterView(RegisterView):
    def get_throttles(self):
        if (
            detect_platform_from_request(self.request) == AuthSourceChoice.DARE
            and registration_approval_enabled()
        ):
            return [AccessRequestThrottle()]
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        if (
            detect_platform_from_request(request) == AuthSourceChoice.DARE
            and registration_approval_enabled()
            and account_settings.EMAIL_VERIFICATION
            != account_settings.EmailVerificationMethod.MANDATORY
        ):
            return Response(
                {
                    "detail": _(
                        "Signup is unavailable because mandatory email verification is not configured. Contact the deployment administrator."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        self.approval_required = serializer.approval_required
        if not self.approval_required:
            return super().perform_create(serializer)
        with transaction.atomic():
            user = serializer.save(self.request)
            signals.user_signed_up.send(
                sender=user.__class__, request=self.request._request, user=user
            )
            # allauth's login flow stops at inactive accounts before sending mail.
            # Request verification explicitly without logging the applicant in.
            email = EmailAddress.objects.get(user=user, email=user.email)
            transaction.on_commit(
                partial(enqueue_signup_verification, email.pk),
                robust=True,
            )
        return user

    def get_response_data(self, user):
        pending = getattr(self, "approval_required", False)
        if not pending:
            data = super().get_response_data(user)
            if (
                account_settings.EMAIL_VERIFICATION
                == account_settings.EmailVerificationMethod.MANDATORY
            ):
                data["email_delivery_configured"] = settings.EMAIL_DELIVERY_CONFIGURED
                data["approval_required"] = False
            return data
        return {
            "detail": _(
                "Account created. Verify your email and wait for administrator approval before signing in."
            ),
            "approval_required": True,
            "email_delivery_configured": settings.EMAIL_DELIVERY_CONFIGURED,
        }
