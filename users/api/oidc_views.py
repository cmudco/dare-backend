"""Single sign-on endpoints: the provider round trip, then tokens for the frontend."""

import jwt
from allauth.account.internal.decorators import login_not_required
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import (OAuth2CallbackView,
                                                          OAuth2LoginView)
from allauth.socialaccount.providers.openid_connect.views import \
    OpenIDConnectOAuth2Adapter
from dj_rest_auth.app_settings import api_settings
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from config import env
from users.api.serializers import SsoCodeSerializer, SsoConfigSerializer
from users.services.oidc_service import (frontend_callback_url,
                                         frontend_login_error_url,
                                         issue_login_code, oidc_enabled,
                                         redeem_login_code,
                                         verified_id_token_claims)
from users.sso import tokens_for


class OIDCAdapter(OpenIDConnectOAuth2Adapter):
    """Adds the verified ID token's claims: some providers send groups only there."""

    def complete_login(self, request, app, token, **kwargs):
        response = (
            get_adapter()
            .get_requests_session()
            .get(self.profile_url, headers={"Authorization": "Bearer " + token.token})
        )
        response.raise_for_status()
        claims = response.json()
        try:
            claims.update(
                verified_id_token_claims(
                    kwargs["response"]["id_token"], self.openid_config
                )
            )
        except jwt.PyJWTError as error:
            raise OAuth2Error("The identity provider's ID token is invalid") from error
        return self.get_provider().sociallogin_from_response(request, claims)


def _provider_view(view_class, request, provider_id):
    try:
        return view_class.adapter_view(OIDCAdapter(request, provider_id))(request)
    except SocialApp.DoesNotExist:
        raise Http404


@login_not_required
def oidc_login(request, provider_id):
    return _provider_view(OAuth2LoginView, request, provider_id)


@login_not_required
def oidc_callback(request, provider_id):
    return _provider_view(OAuth2CallbackView, request, provider_id)


def sso_login(request):
    """Where the frontend's sign-in button points."""
    if not oidc_enabled():
        raise Http404
    login_url = reverse(
        "openid_connect_login", kwargs={"provider_id": env.OIDC_PROVIDER_ID}
    )
    query = urlencode({"process": "login", "next": reverse("api:sso-complete")})
    return redirect(f"{login_url}?{query}")


def sso_complete(request):
    """
    Ends the provider round trip. The session it left on this host is swapped
    for a one-time code, because tokens must not travel in a URL.
    """
    if not request.user.is_authenticated:
        return redirect(frontend_login_error_url("session_missing"))
    code = issue_login_code(request.user)
    logout(request)
    return redirect(frontend_callback_url(code))


class SsoConfigView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        config = {"enabled": oidc_enabled(), "label": env.OIDC_PROVIDER_NAME}
        return Response(SsoConfigSerializer(config).data)


class SsoTokenView(APIView):
    """Trades the one-time code for the same response a password login returns."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = SsoCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = redeem_login_code(serializer.validated_data["code"])
        if user is None:
            return Response(
                {"detail": "This sign-in link has expired. Please sign in again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh, access = tokens_for(user)
        data = {"user": user, "access": str(access), "refresh": str(refresh)}
        return Response(
            api_settings.JWT_SERIALIZER(data, context={"request": request}).data
        )
