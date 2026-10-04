from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import LiteLLMSpend
from billing.test_group_wallet import make_admin, make_group, make_key
from feature_flags.models import FeatureFlag
from users.models import User

ACTIVE_WALLET_URL = "/api/billing/wallets/active/"


class WalletEndpointTests(TestCase):
    def setUp(self):
        for key in ("enable_litellm_wallet", "enable_byok"):
            FeatureFlag.objects.update_or_create(
                key=key, defaults={"default_enabled": True}
            )
        self.group = make_group("CMU-101")
        self.user = User.objects.create_user(
            email="member@example.com", password="x", access_code_group=self.group
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_litellm_stats_labels_a_group_key_with_its_access_code(self):
        key = make_key(self.group, make_admin())
        LiteLLMSpend.objects.create(
            user=self.user,
            litellm_key=key,
            total_reference_amount=Decimal("0.10"),
            call_count=1,
        )

        response = self.client.get("/api/billing/litellm-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["keysBreakdown"][0]["groupName"], "CMU-101")

    def test_a_malformed_wallet_ref_is_a_not_found_not_a_crash(self):
        for wallet_type in (
            UserWalletPreferenceTypeChoice.BYO,
            UserWalletPreferenceTypeChoice.LITELLM,
        ):
            with self.subTest(wallet_type=wallet_type):
                response = self.client.put(
                    ACTIVE_WALLET_URL,
                    {"type": wallet_type, "refId": "invalid"},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "WALLET_NOT_FOUND")
