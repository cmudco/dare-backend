from unittest import mock

from django.db.models.query import QuerySet
from django.test import TestCase

from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import UserWalletPreference
from users.models import User


class GetOrCreateForTests(TestCase):
    def test_concurrent_first_creation_returns_the_existing_row(self):
        user = User.objects.create_user(email="new@example.test", password="x")
        real_get = QuerySet.get
        calls = {"n": 0}

        # Interleaving seen on dev: request A's lookup misses, request B inserts
        # the row, then A's save() runs full_clean() against B's row.
        def get_after_other_request_inserted(qs, *args, **kwargs):
            calls["n"] += 1
            if qs.model is UserWalletPreference and calls["n"] == 1:
                UserWalletPreference.objects.bulk_create(
                    [UserWalletPreference(user=user)]
                )
                raise UserWalletPreference.DoesNotExist
            return real_get(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "get", get_after_other_request_inserted):
            pref = UserWalletPreference.get_or_create_for(user)

        self.assertEqual(pref.user, user)
        self.assertEqual(pref.active_wallet_type, UserWalletPreferenceTypeChoice.DARE)
        self.assertEqual(UserWalletPreference.objects.filter(user=user).count(), 1)
