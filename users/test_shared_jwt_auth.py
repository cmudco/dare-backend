"""
Shared JWT authentication between DARE and Research Tools.

Both products sign with the same key and name the user by email rather than
primary key, so a token issued by either server is accepted by both. The
primary key cannot carry the identity across: this project's user 42 and
Research Tools' user 42 are different people.

What differs between the two sides is provisioning. Research Tools creates an
account for an email it has not seen, because arriving there from here should
just work. This project does not: an account here is granted wallet credit
through an access-code group, so it stays behind the normal signup.
"""

from datetime import timedelta
from uuid import uuid4

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework.test import APIRequestFactory

User = get_user_model()

# Deliberately not SECRET_KEY, which also seeds password-reset tokens --
# sharing that would let either product forge reset links for the other.
SSO_KEY = "shared-sso-key-for-tests-only"
OTHER_KEY = "a-key-neither-product-uses"

SIMPLE_JWT = {
    "SIGNING_KEY": SSO_KEY,
    "ALGORITHM": "HS256",
    "USER_ID_FIELD": "email",
    "USER_ID_CLAIM": "email",
    "LEEWAY": timedelta(seconds=10),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}


def research_tools_token(
    email, *, name="Ada Lovelace", lifetime=timedelta(minutes=5), key=SSO_KEY
):
    """A token shaped like one Research Tools would issue."""
    now = timezone.now()
    return jwt.encode(
        {
            "token_type": "access",
            "email": email,
            "name": name,
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
            "jti": uuid4().hex,
        },
        key,
        algorithm="HS256",
    )


def authenticate(token):
    request = APIRequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {token}")
    return JWTAuthentication().authenticate(request)


@override_settings(SIMPLE_JWT=SIMPLE_JWT)
class ForeignTokenTests(TestCase):
    """A token this project never issued still identifies its user."""

    def test_a_research_tools_token_authenticates_an_existing_user(self):
        User.objects.create(email="ada@example.com")
        user, _ = authenticate(research_tools_token("ada@example.com"))
        self.assertEqual(user.email, "ada@example.com")

    def test_an_unknown_email_is_rejected(self):
        """
        The asymmetry that matters. Creating a user here grants wallet credit
        through the access-code group, so a token alone must not be able to.
        """
        with self.assertRaises(AuthenticationFailed):
            authenticate(research_tools_token("stranger@example.com"))

    def test_an_unknown_email_creates_no_account(self):
        with self.assertRaises(AuthenticationFailed):
            authenticate(research_tools_token("stranger@example.com"))
        self.assertFalse(User.objects.filter(email="stranger@example.com").exists())

    def test_a_token_signed_with_another_key_is_rejected(self):
        User.objects.create(email="ada@example.com")
        with self.assertRaises(InvalidToken):
            authenticate(research_tools_token("ada@example.com", key=OTHER_KEY))

    def test_an_expired_token_is_rejected(self):
        User.objects.create(email="ada@example.com")
        with self.assertRaises(InvalidToken):
            authenticate(
                research_tools_token("ada@example.com", lifetime=timedelta(minutes=-5))
            )

    def test_a_disabled_account_is_rejected(self):
        User.objects.create(email="ada@example.com", is_active=False)
        with self.assertRaises(AuthenticationFailed):
            authenticate(research_tools_token("ada@example.com"))


@override_settings(SIMPLE_JWT=SIMPLE_JWT)
class IssuedTokenClaimTests(TestCase):
    """
    What this project puts in a token, so Research Tools can act on it.

    The email identifies the person. The name is there because a user arriving
    at Research Tools for the first time has no account, and it is built from
    these claims -- without a name they land with a blank one.
    """

    def setUp(self):
        self.user = User.objects.create(
            email="ada@example.com", first_name="Ada", last_name="Lovelace"
        )

    def _claims(self, token):
        return jwt.decode(str(token), SSO_KEY, algorithms=["HS256"])

    def test_the_identity_claim_is_the_email(self):
        from users.sso import tokens_for

        refresh, access = tokens_for(self.user)
        self.assertEqual(self._claims(access)["email"], "ada@example.com")

    def test_the_name_travels_with_the_token(self):
        from users.sso import tokens_for

        refresh, access = tokens_for(self.user)
        self.assertEqual(self._claims(access)["name"], "Ada Lovelace")

    def test_the_refresh_token_carries_the_name_too(self):
        """
        A refreshed access token is derived from the refresh token, so a claim
        missing there would quietly disappear the first time a session renews.
        """
        from users.sso import tokens_for

        refresh, _access = tokens_for(self.user)
        self.assertEqual(self._claims(refresh)["name"], "Ada Lovelace")

    def test_a_refreshed_access_token_still_carries_the_name(self):
        from users.sso import tokens_for

        refresh, _access = tokens_for(self.user)
        self.assertEqual(self._claims(refresh.access_token)["name"], "Ada Lovelace")

    def test_a_user_with_no_name_gets_an_empty_claim(self):
        from users.sso import tokens_for

        nameless = User.objects.create(email="nobody@example.com")
        _refresh, access = tokens_for(nameless)
        self.assertEqual(self._claims(access)["name"], "")

    def test_the_login_endpoint_issues_a_token_carrying_the_name(self):
        """
        Login goes through dj-rest-auth, not this project's own code, so the
        claim has to be attached where dj-rest-auth looks for it.
        """
        from dj_rest_auth.utils import jwt_encode

        access, _refresh = jwt_encode(self.user)
        self.assertEqual(self._claims(access)["name"], "Ada Lovelace")
        self.assertEqual(self._claims(access)["email"], "ada@example.com")
