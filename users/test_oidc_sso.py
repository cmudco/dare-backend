"""Single sign-on: who gets in, which role they get, and how tokens reach the frontend."""

from unittest.mock import patch

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from rest_framework.test import APIClient

from config import env
from users.adapter import SocialAccountAdapter
from users.constants import RoleChoice
from users.role_service import role_for_groups
from users.services.oidc_service import issue_login_code

User = get_user_model()

ROLE_MAP = {"admins": RoleChoice.SUPERADMIN, "leads": RoleChoice.SUPERVISOR}


def sociallogin_for(user, **claims):
    claims.setdefault("email", user.email)
    account = SocialAccount(provider="city", uid="subject-1", extra_data=claims)
    return SocialLogin(user=user, account=account)


class RoleForGroupsTests(SimpleTestCase):
    def test_highest_mapped_role_wins(self):
        self.assertEqual(
            role_for_groups(["leads", "admins", "other"], ROLE_MAP),
            RoleChoice.SUPERADMIN,
        )

    def test_no_mapped_group_is_a_plain_user(self):
        self.assertEqual(role_for_groups(["other"], ROLE_MAP), RoleChoice.USER)


class SignInPolicyTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/")
        self.adapter = SocialAccountAdapter()

    def refusal(self, sociallogin):
        with self.assertRaises(ImmediateHttpResponse) as raised:
            self.adapter.pre_social_login(self.request, sociallogin)
        return raised.exception.response.url

    @patch.object(env, "OIDC_ALLOWED_EMAIL_DOMAINS", ["city.test"])
    def test_other_email_domains_are_refused(self):
        outsider = User(email="someone@other.test")
        self.assertIn(
            "sso_error=domain_not_allowed", self.refusal(sociallogin_for(outsider))
        )

    def test_unverified_email_is_refused(self):
        newcomer = User(email="new@city.test")
        login = sociallogin_for(newcomer, email_verified=False)
        self.assertIn("sso_error=email_unverified", self.refusal(login))

    @patch("users.adapter.OIDC_AUTO_CREATE_USERS", False)
    def test_unknown_person_is_refused_when_accounts_are_not_auto_created(self):
        newcomer = User(email="new@city.test")
        self.assertIn(
            "sso_error=signup_closed", self.refusal(sociallogin_for(newcomer))
        )

    def test_deactivated_account_is_refused(self):
        user = User.objects.create_user(email="gone@city.test", is_active=False)
        self.assertIn("sso_error=account_inactive", self.refusal(sociallogin_for(user)))

    @patch.object(env, "OIDC_ROLE_MAP", ROLE_MAP)
    def test_role_follows_the_providers_groups_on_each_sign_in(self):
        user = User.objects.create_user(email="staff@city.test")

        self.adapter.pre_social_login(
            self.request, sociallogin_for(user, groups=["leads"])
        )
        user.refresh_from_db()
        self.assertEqual(user.platform_role, RoleChoice.SUPERVISOR)

        self.adapter.pre_social_login(self.request, sociallogin_for(user, groups=[]))
        user.refresh_from_db()
        self.assertEqual(user.platform_role, RoleChoice.USER)

    @patch.object(env, "OIDC_ROLE_MAP", {})
    def test_roles_stay_as_set_in_dare_without_a_role_map(self):
        user = User.objects.create_user(
            email="researcher@city.test", platform_role=RoleChoice.RESEARCHER
        )
        self.adapter.pre_social_login(
            self.request, sociallogin_for(user, groups=["leads"])
        )
        user.refresh_from_db()
        self.assertEqual(user.platform_role, RoleChoice.RESEARCHER)


class LoginCodeTests(TestCase):
    def test_code_buys_tokens_exactly_once(self):
        user = User.objects.create_user(email="staff@city.test")
        code = issue_login_code(user)
        client = APIClient()

        first = client.post("/users/api/sso/token/", {"code": code}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["user"]["email"], user.email)
        self.assertTrue(first.json()["access"])

        second = client.post("/users/api/sso/token/", {"code": code}, format="json")
        self.assertEqual(second.status_code, 400)
