from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from billing.caps import refill_credit
from billing.constants import (
    LITELLM_SPEND_LIMIT_REACHED,
    LiteLLMKeySourceChoice,
    PolicySourceChoice,
    TransactionSourceChoice,
    UserWalletPreferenceTypeChoice,
)
from billing.exceptions import PaymentRequiredError
from billing.gateway_report import (
    GatewayKeyReport,
    parse_gateway_key_report,
    record_gateway_key_report,
)
from billing.models import (
    GroupWallet,
    LiteLLMKey,
    LiteLLMSpend,
    SystemRefillPolicy,
    Transaction,
    UserRefillOverride,
    UserWalletPreference,
    Wallet,
)
from billing.services import WalletService
from billing.tasks import process_scheduled_refills
from conversations.models import LLM
from conversations.services.message_coordinator import MessageCoordinator
from core.services.api_key_service import get_dispatch_credentials_for_user_sync
from core.services.background_model_service import (
    BackgroundModelRoute,
    BackgroundModelService,
    BackgroundModelUnavailable,
)
from core.services.custom_llm_service import CustomLLMService
from feature_flags.models import FeatureFlag
from users.models import AccessCodeGroup, User

GROUP_WALLETS = "/api/billing/group-wallets/"


def make_user(email, group=None, balance="0.00"):
    user = User.objects.create_user(email=email, password="x", access_code_group=group)
    Wallet.objects.filter(user=user).update(balance=Decimal(balance))
    return user


def make_group_key(group, created_by, label="course gateway"):
    return LiteLLMKey.objects.create(
        label=label,
        base_url="https://gateway.example/v1",
        api_key="group-key",
        source=LiteLLMKeySourceChoice.ADMIN_GROUP,
        source_group=group,
        created_by=created_by,
    )


def make_personal_key(user):
    return LiteLLMKey.objects.create(
        label="my key",
        base_url="https://gateway.example/v1",
        api_key="personal-key",
        source=LiteLLMKeySourceChoice.USER,
        owner_user=user,
        created_by=user,
    )


def record_spend(user, key, amount):
    LiteLLMSpend.objects.update_or_create(
        user=user,
        litellm_key=key,
        defaults={"total_reference_amount": Decimal(amount), "call_count": 1},
    )


def activate(user, key):
    pref = UserWalletPreference.get_or_create_for(user)
    pref.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
    pref.active_wallet_ref_id = str(key.pk)
    pref.save()


class RefillCreditTests(SimpleTestCase):
    def test_no_cap_adds_the_full_amount(self):
        self.assertEqual(refill_credit(Decimal("5"), None, Decimal("40")), Decimal("5"))

    def test_tops_up_only_to_the_cap(self):
        self.assertEqual(
            refill_credit(Decimal("5"), Decimal("5"), Decimal("3")), Decimal("2")
        )

    def test_nothing_is_added_at_or_above_the_cap(self):
        self.assertEqual(refill_credit(Decimal("5"), Decimal("5"), Decimal("5")), 0)
        self.assertEqual(refill_credit(Decimal("5"), Decimal("5"), Decimal("9")), 0)

    def test_a_full_refill_below_the_cap_is_untouched(self):
        self.assertEqual(
            refill_credit(Decimal("5"), Decimal("20"), Decimal("1")), Decimal("5")
        )


class ScheduledRefillCapTests(TestCase):
    """Refills stop filling at the cap and charge the group only for what they add."""

    def setUp(self):
        self.owner = make_user("prof@example.com")
        self.group = AccessCodeGroup.objects.create(
            access_code="CAP-101", max_capacity=50, group_owner=self.owner
        )
        self.group_wallet = GroupWallet.objects.create(
            group=self.group,
            budget_balance=Decimal("100"),
            refill_amount=Decimal("5"),
            refill_cap=Decimal("5"),
        )

    def _refill(self):
        process_scheduled_refills()
        self.group_wallet.refresh_from_db()

    def test_member_below_the_cap_is_topped_up_to_it(self):
        member = make_user("low@example.com", self.group, balance="3.00")
        self._refill()

        self.assertEqual(Wallet.objects.get(user=member).balance, Decimal("5"))
        self.assertEqual(self.group_wallet.budget_balance, Decimal("98"))

    def test_member_at_the_cap_gets_nothing_but_the_period_is_served(self):
        member = make_user("full@example.com", self.group, balance="5.00")
        self._refill()

        wallet = Wallet.objects.get(user=member)
        self.assertEqual(wallet.balance, Decimal("5"))
        self.assertIsNotNone(wallet.last_refill_at)
        self.assertEqual(self.group_wallet.budget_balance, Decimal("100"))
        self.assertFalse(
            Transaction.objects.filter(
                user=member, source=TransactionSourceChoice.SCHEDULED_REFILL
            ).exists()
        )

    def test_without_a_cap_refills_still_add_the_full_amount(self):
        self.group_wallet.refill_cap = None
        self.group_wallet.save()
        member = make_user("rich@example.com", self.group, balance="40.00")
        self._refill()

        self.assertEqual(Wallet.objects.get(user=member).balance, Decimal("45"))

    def test_member_override_cap_beats_the_group_cap(self):
        member = make_user("ta@example.com", self.group, balance="5.00")
        UserRefillOverride.objects.create(user=member, refill_cap=Decimal("20"))
        self._refill()

        self.assertEqual(Wallet.objects.get(user=member).balance, Decimal("10"))

    def test_system_cap_applies_to_users_without_a_group(self):
        policy = SystemRefillPolicy.load()
        policy.refill_cap = Decimal("5")
        policy.save()
        loner = make_user("solo@example.com", balance="4.00")
        self._refill()

        self.assertEqual(Wallet.objects.get(user=loner).balance, Decimal("5"))
        self.assertEqual(
            WalletService.get_effective_refill_policy(loner).cap_source,
            PolicySourceChoice.SYSTEM,
        )

    def test_served_period_is_not_refilled_again_the_next_day(self):
        member = make_user("spender@example.com", self.group, balance="5.00")
        self._refill()
        Wallet.objects.filter(user=member).update(balance=Decimal("1"))
        self._refill()

        self.assertEqual(Wallet.objects.get(user=member).balance, Decimal("1"))


class SpendLimitGateTests(TestCase):
    """A member cannot dispatch through the group key once their limit is used."""

    def setUp(self):
        FeatureFlag.objects.update_or_create(
            key="enable_litellm_wallet", defaults={"default_enabled": True}
        )
        self.admin = make_user("admin@example.com")
        self.group = AccessCodeGroup.objects.create(
            access_code="GEN-111", max_capacity=200
        )
        GroupWallet.objects.create(group=self.group, litellm_member_cap=Decimal("15"))
        self.key = make_group_key(self.group, self.admin)
        self.student = make_user("student@example.com", self.group)
        activate(self.student, self.key)

    def _dispatch(self):
        return get_dispatch_credentials_for_user_sync("custom", self.student)

    def test_member_under_the_limit_dispatches_with_attribution(self):
        record_spend(self.student, self.key, "14.99")
        creds = self._dispatch()

        self.assertEqual(creds.litellm_key_id, str(self.key.pk))
        self.assertEqual(creds.gateway_user, f"dare-user-{self.student.pk}")

    def test_member_at_the_limit_is_refused(self):
        record_spend(self.student, self.key, "15.00")
        with self.assertRaises(PaymentRequiredError) as caught:
            self._dispatch()

        self.assertEqual(caught.exception.code, LITELLM_SPEND_LIMIT_REACHED)
        self.assertEqual(caught.exception.details["limit"], "15.000000")

    def test_spend_on_a_rotated_group_key_still_counts(self):
        old_key = make_group_key(self.group, self.admin, label="old")
        record_spend(self.student, old_key, "10")
        record_spend(self.student, self.key, "5")

        with self.assertRaises(PaymentRequiredError):
            self._dispatch()

    def test_personal_key_spend_neither_counts_nor_is_limited(self):
        personal = make_personal_key(self.student)
        record_spend(self.student, personal, "50")
        self.assertEqual(
            WalletService.get_litellm_spend_limit(self.student).used, Decimal("0")
        )

        activate(self.student, personal)
        self.assertEqual(self._dispatch().litellm_key_id, str(personal.pk))

    def test_instructor_override_raises_the_limit(self):
        record_spend(self.student, self.key, "15")
        UserRefillOverride.objects.create(user=self.student, litellm_cap=Decimal("30"))

        self.assertEqual(self._dispatch().litellm_key_id, str(self.key.pk))

    def test_a_group_without_a_limit_is_unlimited(self):
        GroupWallet.objects.filter(group=self.group).update(litellm_member_cap=None)
        record_spend(self.student, self.key, "500")

        self.assertEqual(self._dispatch().litellm_key_id, str(self.key.pk))

    def test_limit_change_applies_to_a_user_object_loaded_earlier(self):
        record_spend(self.student, self.key, "15")
        session_user = User.objects.select_related(
            "access_code_group__group_wallet"
        ).get(pk=self.student.pk)
        self.assertEqual(
            session_user.access_code_group.group_wallet.litellm_member_cap,
            Decimal("15"),
        )
        GroupWallet.objects.filter(group=self.group).update(
            litellm_member_cap=Decimal("40")
        )

        creds = get_dispatch_credentials_for_user_sync("custom", session_user)
        self.assertEqual(creds.litellm_key_id, str(self.key.pk))

    def test_chat_preflight_reports_the_limit_and_passes_otherwise(self):
        self.assertIsNone(MessageCoordinator._spend_limit_error(self.student))

        record_spend(self.student, self.key, "15")
        error = MessageCoordinator._spend_limit_error(self.student)
        self.assertEqual(error.code, LITELLM_SPEND_LIMIT_REACHED)
        self.assertIn("$15.00", str(error))

    def test_background_work_reports_unavailable_instead_of_billing_errors(self):
        route = BackgroundModelRoute(
            model=LLM(identifier="x", provider="custom"),
            wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
            dispatch_user=self.student,
        )
        refused = PaymentRequiredError("limit", code=LITELLM_SPEND_LIMIT_REACHED)
        with patch(
            "core.services.llm_service.LLMService._get_ai_service",
            AsyncMock(side_effect=refused),
        ):
            with self.assertRaises(BackgroundModelUnavailable):
                async_to_sync(BackgroundModelService._service_for)(route)


class GroupWalletApiTests(TestCase):
    def setUp(self):
        self.owner = make_user("prof@example.com")
        self.group = AccessCodeGroup.objects.create(
            access_code="GEN-112", max_capacity=200, group_owner=self.owner
        )
        self.group_wallet = GroupWallet.objects.create(group=self.group)
        self.key = make_group_key(self.group, self.owner)
        self.student = make_user("student@example.com", self.group)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _patch(self, body):
        return self.client.patch(
            f"{GROUP_WALLETS}{self.group_wallet.pk}/", body, format="json"
        )

    def test_owner_sets_and_clears_both_caps(self):
        response = self._patch({"litellmMemberCap": "15", "refillCap": "5"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["litellmMemberCap"], "15.000000")
        self.group_wallet.refresh_from_db()
        self.assertEqual(self.group_wallet.refill_cap, Decimal("5"))

        self._patch({"clearLitellmMemberCap": True})
        self.group_wallet.refresh_from_db()
        self.assertIsNone(self.group_wallet.litellm_member_cap)
        self.assertEqual(self.group_wallet.refill_cap, Decimal("5"))

    def test_negative_cap_is_rejected(self):
        response = self._patch({"litellmMemberCap": "-1"})
        self.assertEqual(response.status_code, 400)

    def test_another_owner_cannot_see_or_change_the_group(self):
        stranger = make_user("other-prof@example.com")
        self.client.force_authenticate(stranger)

        self.assertEqual(self._patch({"litellmMemberCap": "99"}).status_code, 404)
        self.group_wallet.refresh_from_db()
        self.assertIsNone(self.group_wallet.litellm_member_cap)

    def test_members_show_their_use_against_the_limit(self):
        self._patch({"litellmMemberCap": "15"})
        record_spend(self.student, self.key, "4.5")

        rows = self.client.get(f"{GROUP_WALLETS}{self.group_wallet.pk}/members/").json()
        row = next(r for r in rows if r["id"] == self.student.pk)
        self.assertEqual(row["spendLimit"]["used"], "4.500000")
        self.assertEqual(row["spendLimit"]["remaining"], "10.500000")
        self.assertEqual(row["spendLimit"]["source"], PolicySourceChoice.GROUP)
        self.assertFalse(row["spendLimit"]["isReached"])

    def test_member_override_sets_and_clears_the_spend_limit(self):
        url = (
            f"{GROUP_WALLETS}{self.group_wallet.pk}/members/{self.student.pk}/override/"
        )
        response = self.client.put(url, {"litellmCap": "30"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["member"]["spendLimit"]["source"], PolicySourceChoice.USER
        )

        self.client.put(url, {"clearLitellmCap": True}, format="json")
        self.assertFalse(UserRefillOverride.objects.filter(user=self.student).exists())

    def test_gateway_figures_sit_beside_the_dare_estimate(self):
        record_spend(self.student, self.key, "2.25")
        record_gateway_key_report(
            str(self.key.pk),
            GatewayKeyReport(spend=Decimal("2.4"), max_budget=Decimal("1740")),
        )

        body = self.client.get(f"{GROUP_WALLETS}owned/").json()[0]["groupWallet"]
        (gateway_key,) = body["gatewayKeys"]
        self.assertEqual(gateway_key["dareEstimate"], "2.250000")
        self.assertEqual(gateway_key["gatewaySpend"], "2.400000")
        self.assertEqual(gateway_key["gatewayMaxBudget"], "1740.000000")

    def test_student_wallet_list_shows_their_allowance(self):
        FeatureFlag.objects.update_or_create(
            key="enable_litellm_wallet", defaults={"default_enabled": True}
        )
        self.group_wallet.litellm_member_cap = Decimal("15")
        self.group_wallet.save()
        record_spend(self.student, self.key, "1")
        self.client.force_authenticate(self.student)

        wallets = self.client.get("/api/billing/wallets/").json()["wallets"]
        group_row = next(w for w in wallets if w.get("refId") == str(self.key.pk))
        self.assertEqual(group_row["status"]["spendLimit"]["remaining"], "14.000000")


class GatewayReportTests(TestCase):
    def setUp(self):
        owner = make_user("owner@example.com")
        self.key = make_personal_key(owner)

    def test_parses_the_key_totals(self):
        report = parse_gateway_key_report(
            {"x-litellm-key-spend": "9.265e-05", "x-litellm-key-max-budget": "5.0"}
        )
        self.assertEqual(report.spend, Decimal("9.265e-05"))
        self.assertEqual(report.max_budget, Decimal("5.0"))

    def test_no_spend_header_means_no_report(self):
        self.assertIsNone(parse_gateway_key_report({"x-litellm-key-max-budget": "5"}))
        self.assertIsNone(parse_gateway_key_report({"x-litellm-key-spend": "nan"}))

    def test_a_stale_lower_total_does_not_overwrite_a_newer_one(self):
        record_gateway_key_report(
            str(self.key.pk), GatewayKeyReport(Decimal("2"), None)
        )
        record_gateway_key_report(
            str(self.key.pk), GatewayKeyReport(Decimal("1"), None)
        )

        self.key.refresh_from_db()
        self.assertEqual(self.key.gateway_spend, Decimal("2"))
        self.assertIsNotNone(self.key.gateway_reported_at)

    def test_transport_tags_the_user_and_records_the_response_report(self):
        service = CustomLLMService(
            llm=LLM(identifier="gemini/flash", name="flash", provider="custom"),
            api_key="k",
            base_url="https://gateway.example/v1",
            litellm_key_id=str(self.key.pk),
            gateway_user="dare-user-7",
        )
        params = service._build_chat_completion_params([], 10, 0.2)
        self.assertEqual(params["user"], "dare-user-7")

        response = httpx.Response(200, headers={"x-litellm-key-spend": "0.5"})
        async_to_sync(service._record_gateway_report)(response)
        self.key.refresh_from_db()
        self.assertEqual(self.key.gateway_spend, Decimal("0.5"))

    def test_transport_without_a_key_sends_no_user(self):
        service = CustomLLMService(
            llm=LLM(identifier="m", name="m", provider="custom"),
            api_key="k",
            base_url="https://custom.example/v1",
        )
        self.assertNotIn("user", service._build_chat_completion_params([], 10, 0.2))


class EffectivePolicyCapTests(TestCase):
    def test_cap_falls_through_user_group_then_system(self):
        group = AccessCodeGroup.objects.create(access_code="TIER-1", max_capacity=5)
        GroupWallet.objects.create(group=group, refill_cap=Decimal("8"))
        member = make_user("member@example.com", group)

        policy = WalletService.get_effective_refill_policy(member)
        self.assertEqual(
            (policy.cap, policy.cap_source), (Decimal("8"), PolicySourceChoice.GROUP)
        )

        UserRefillOverride.objects.create(user=member, refill_cap=Decimal("3"))
        member = User.objects.get(pk=member.pk)
        policy = WalletService.get_effective_refill_policy(member)
        self.assertEqual(
            (policy.cap, policy.cap_source), (Decimal("3"), PolicySourceChoice.USER)
        )

    def test_no_cap_anywhere_resolves_to_none(self):
        loner = make_user("none@example.com")
        self.assertIsNone(WalletService.get_effective_refill_policy(loner).cap)


class GroupOwnerKeyTests(TestCase):
    """The owner runs the course on the group's key without joining it."""

    def setUp(self):
        FeatureFlag.objects.update_or_create(
            key="enable_litellm_wallet", defaults={"default_enabled": True}
        )
        self.owner = make_user("prof@example.com")
        self.group = AccessCodeGroup.objects.create(
            access_code="OWN-101", max_capacity=50, group_owner=self.owner
        )
        GroupWallet.objects.create(group=self.group, litellm_member_cap=Decimal("1"))

    def _active_ref(self, user):
        return UserWalletPreference.get_or_create_for(user).active_wallet_ref_id

    def test_owner_sees_and_is_moved_onto_a_newly_issued_key(self):
        key = make_group_key(self.group, self.owner)

        self.assertIn(key, LiteLLMKey.visible_for_user(self.owner))
        self.assertEqual(self._active_ref(self.owner), str(key.pk))

    def test_owner_assigned_later_is_moved_onto_the_existing_key(self):
        key = make_group_key(self.group, self.owner)
        new_owner = make_user("new-prof@example.com")
        self.group.group_owner = new_owner
        self.group.save()

        self.assertEqual(self._active_ref(new_owner), str(key.pk))

    def test_resaving_the_group_leaves_an_owner_who_chose_dare_alone(self):
        make_group_key(self.group, self.owner)
        UserWalletPreference.get_or_create_for(self.owner).reset_to_dare()
        self.group.notes = "edited"
        self.group.save()

        self.assertIsNone(self._active_ref(self.owner))

    def test_owner_is_never_held_to_the_member_limit(self):
        key = make_group_key(self.group, self.owner)
        record_spend(self.owner, key, "50")
        self.assertEqual(
            get_dispatch_credentials_for_user_sync("custom", self.owner).litellm_key_id,
            str(key.pk),
        )

        self.owner.access_code_group = self.group
        self.owner.save()
        self.assertIsNone(WalletService.get_litellm_spend_limit(self.owner))
        get_dispatch_credentials_for_user_sync("custom", self.owner)

    def test_members_stay_limited_on_the_same_key(self):
        key = make_group_key(self.group, self.owner)
        student = make_user("student@example.com", self.group)
        activate(student, key)
        record_spend(student, key, "1")

        with self.assertRaises(PaymentRequiredError):
            get_dispatch_credentials_for_user_sync("custom", student)

    def test_an_unrelated_user_does_not_see_the_key(self):
        make_group_key(self.group, self.owner)
        outsider = make_user("outsider@example.com")

        self.assertFalse(LiteLLMKey.visible_for_user(outsider).exists())
