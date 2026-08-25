from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.group_wallet import adopt_group_wallet, group_default_key
from billing.models import LiteLLMKey, UserWalletPreference
from feature_flags.models import FeatureFlag
from users.models import AccessCodeGroup, User


def make_group(code="TEST-101"):
    return AccessCodeGroup.objects.create(access_code=code, max_capacity=50)


def make_admin(email="admin@example.com"):
    return User.objects.create_user(email=email, password="x", is_staff=True)


def make_key(group, admin, label="gateway"):
    return LiteLLMKey.objects.create(
        label=label,
        base_url="https://proxy.example/v1",
        api_key="k",
        source=LiteLLMKeySourceChoice.ADMIN_GROUP,
        source_group=group,
        created_by=admin,
    )


def enable_litellm_wallet():
    FeatureFlag.objects.update_or_create(
        key="enable_litellm_wallet",
        defaults={"default_enabled": True},
    )


class GroupDefaultKeyTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.admin = make_admin()

    def test_no_key_means_no_default(self):
        self.assertIsNone(group_default_key(self.group))

    def test_single_key_is_the_default(self):
        key = make_key(self.group, self.admin)
        self.assertEqual(group_default_key(self.group), key)

    def test_two_keys_resolve_to_nothing(self):
        # Guessing here would bill a cohort to the wrong institutional account.
        make_key(self.group, self.admin, label="one")
        make_key(self.group, self.admin, label="two")
        self.assertIsNone(group_default_key(self.group))

    def test_expired_key_does_not_hide_the_only_usable_key(self):
        expired = make_key(self.group, self.admin, label="expired")
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at", "updated_at"])
        usable = make_key(self.group, self.admin, label="usable")

        self.assertEqual(group_default_key(self.group), usable)

    def test_expired_key_does_not_make_two_usable_keys_look_unambiguous(self):
        expired = make_key(self.group, self.admin, label="expired")
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at", "updated_at"])
        make_key(self.group, self.admin, label="one")
        make_key(self.group, self.admin, label="two")

        self.assertIsNone(group_default_key(self.group))

    def test_ungrouped_user_has_no_default(self):
        self.assertIsNone(group_default_key(None))


class AdoptGroupWalletTests(TestCase):
    def setUp(self):
        enable_litellm_wallet()
        self.group = make_group()
        self.admin = make_admin()
        self.user = User.objects.create_user(
            email="member@example.com", password="x", access_code_group=self.group
        )

    def test_member_defaults_onto_the_group_key(self):
        key = make_key(self.group, self.admin)
        joiner = User.objects.create_user(
            email="joiner@example.com", password="x", access_code_group=self.group
        )
        self.assertTrue(adopt_group_wallet(joiner))

        pref = UserWalletPreference.get_or_create_for(joiner)
        self.assertEqual(
            pref.active_wallet_type, UserWalletPreferenceTypeChoice.LITELLM
        )
        self.assertEqual(pref.active_wallet_ref_id, str(key.pk))

    def test_a_personal_key_choice_is_never_overwritten(self):
        make_key(self.group, self.admin)
        chooser = User.objects.create_user(
            email="chooser@example.com", password="x", access_code_group=self.group
        )
        personal = LiteLLMKey.objects.create(
            label="my own",
            base_url="https://mine.example/v1",
            api_key="k",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=chooser,
            created_by=chooser,
        )
        pref = UserWalletPreference.get_or_create_for(chooser)
        pref.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
        pref.active_wallet_ref_id = str(personal.pk)
        pref.save()

        self.assertFalse(adopt_group_wallet(chooser))
        pref.refresh_from_db()
        self.assertEqual(pref.active_wallet_ref_id, str(personal.pk))

    def test_user_without_a_group_is_left_alone(self):
        loner = User.objects.create_user(email="solo@example.com", password="x")
        self.assertFalse(adopt_group_wallet(loner))


class GroupKeyProvisioningTests(TestCase):
    """Issuing a key must reach members who signed up before it existed."""

    def test_existing_members_are_adopted_when_the_key_is_created(self):
        enable_litellm_wallet()
        group = make_group("TEST-202")
        member = User.objects.create_user(
            email="early@example.com", password="x", access_code_group=group
        )
        outsider = User.objects.create_user(email="other@example.com", password="x")

        key = make_key(group, make_admin("admin2@example.com"))

        member_pref = UserWalletPreference.get_or_create_for(member)
        self.assertEqual(
            member_pref.active_wallet_type, UserWalletPreferenceTypeChoice.LITELLM
        )
        self.assertEqual(member_pref.active_wallet_ref_id, str(key.pk))

        outsider_pref = UserWalletPreference.get_or_create_for(outsider)
        self.assertEqual(
            outsider_pref.active_wallet_type, UserWalletPreferenceTypeChoice.DARE
        )
