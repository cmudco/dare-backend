import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from billing.models import Wallet
from core.services.billing_service import BillingService
from users.models import User


class BillingWalletLookupTests(SimpleTestCase):
    def setUp(self):
        self.user = User(pk=863)
        self.wallet = Wallet(user=self.user, balance=Decimal("2.00"))
        self.service = BillingService()

    async def test_existing_wallet_is_returned_without_creation(self):
        with patch.object(Wallet.objects, "get_or_create") as create:
            wallet = await self.service._get_user_wallet(self.user)

        self.assertIs(wallet, self.wallet)
        create.assert_not_called()

    async def test_missing_wallet_uses_get_or_create_with_existing_default(self):
        self.user._state.fields_cache["wallet"] = None
        with patch.object(
            Wallet.objects, "get_or_create", return_value=(self.wallet, True)
        ) as create:
            wallet = await self.service._get_user_wallet(self.user)

        self.assertIs(wallet, self.wallet)
        create.assert_called_once_with(
            user=self.user, defaults={"balance": Decimal("5.00")}
        )

    async def test_wallet_created_by_another_request_is_not_reset(self):
        self.user._state.fields_cache["wallet"] = None
        with patch.object(
            Wallet.objects, "get_or_create", return_value=(self.wallet, False)
        ):
            wallet = await self.service._get_user_wallet(self.user)

        self.assertIs(wallet, self.wallet)
        self.assertEqual(wallet.balance, Decimal("2.00"))

    async def test_stream_cancellation_is_not_masked_as_a_credit_error(self):
        with (
            patch(
                "core.services.billing_service.database_sync_to_async",
                return_value=AsyncMock(side_effect=asyncio.CancelledError),
            ),
            patch.object(Wallet.objects, "get_or_create") as create,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.check_streaming_credit_usage(self.user, None, {})

        create.assert_not_called()

    async def test_database_failure_preserves_original_exception(self):
        error = DatabaseError("wallet lookup unavailable")
        with (
            patch(
                "core.services.billing_service.database_sync_to_async",
                return_value=AsyncMock(side_effect=error),
            ),
            patch.object(Wallet.objects, "get_or_create") as create,
        ):
            with self.assertRaises(DatabaseError) as raised:
                await self.service._get_user_wallet(self.user)

        self.assertIs(raised.exception, error)
        create.assert_not_called()
