"""
The claims this project's tokens carry for Research Tools.

Both products sign with the same key and name the user by email, so a token
from here authenticates there. A user arriving at Research Tools for the first
time has no account yet and one is built from these claims -- which is why the
name travels with the token rather than being looked up afterwards. There is
no API between the two products to look it up through.

Provisioning is deliberately one-way. Research Tools creates an account for an
email it has not seen; this project does not, because an account here is
granted wallet credit through an access-code group and a token alone must not
be able to do that.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

NAME_CLAIM = "name"


def add_shared_claims(token, user):
    """Everything the other product needs that the identity claim does not say."""
    token[NAME_CLAIM] = user.get_full_name() or ""
    return token


def tokens_for(user):
    """
    A ``(refresh, access)`` pair carrying the shared claims.

    The name is set on the refresh token, not just the access token: a renewed
    access token is derived from the refresh token, so a claim set only on the
    access token would disappear the first time a session renews.
    """
    refresh = add_shared_claims(RefreshToken.for_user(user), user)
    return refresh, refresh.access_token


class SharedClaimsTokenSerializer(TokenObtainPairSerializer):
    """Wired in as dj-rest-auth's JWT_TOKEN_CLAIMS_SERIALIZER, so login agrees."""

    @classmethod
    def get_token(cls, user):
        return add_shared_claims(super().get_token(user), user)


def user_for_access_token(raw_token):
    """The active user an access token names, validated as the REST API does, else None."""
    auth = JWTAuthentication()
    try:
        return auth.get_user(auth.get_validated_token(raw_token))
    except (InvalidToken, AuthenticationFailed):
        return None
