"""
Characterization tests for SyftBoxTokenMixin and syftbox_jwt_expired.

This is the piece the shared package replaces with its own token table, so it
is the highest-risk thing to move: the refresh-and-save path here runs on every
authenticated SyftBox call. Whatever the package does instead has to reproduce
these outcomes exactly, including which fields get written and when.

Uses the real User model rather than a stub, since the encrypted field types
and the `save(update_fields=...)` call are part of what is being pinned.
"""

import time
from unittest import mock

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase

from syftbox.dtos import AuthTokens
from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from users.utils import syftbox_jwt_expired

User = get_user_model()
# The implementation now lives in the shared package; DARE's syftbox.mixins
# is a re-export shim, so the patch target follows the code.
REFRESH = "syftbox_connect.mixins.SyftBoxAuthService.refresh_token"


def token(exp_offset_seconds=None, **claims):
    """A signature-free JWT; the helper never verifies, so the key is irrelevant."""
    payload = dict(claims)
    if exp_offset_seconds is not None:
        payload["exp"] = time.time() + exp_offset_seconds
    return jwt.encode(payload, "irrelevant", algorithm="HS256")


class JwtExpiryTests(TestCase):
    def test_a_token_expiring_well_in_the_future_is_not_expired(self):
        self.assertFalse(syftbox_jwt_expired(token(3600)))

    def test_a_token_already_past_expiry_is_expired(self):
        self.assertTrue(syftbox_jwt_expired(token(-10)))

    def test_a_token_inside_the_leeway_window_counts_as_expired(self):
        """60s default leeway -- a token expiring in 30s is refreshed early."""
        self.assertTrue(syftbox_jwt_expired(token(30)))

    def test_the_leeway_is_configurable(self):
        self.assertFalse(syftbox_jwt_expired(token(30), leeway_seconds=5))

    def test_a_token_without_an_exp_claim_is_treated_as_valid(self):
        """QUIRK: no exp means never refreshed, however old the token is."""
        self.assertFalse(syftbox_jwt_expired(token(None, sub="abc")))

    def test_an_unparseable_token_is_treated_as_valid(self):
        """
        QUIRK, and the consequential one. A malformed or truncated token
        returns False -- "not expired" -- so the mixin hands it straight to the
        API rather than refreshing. The user sees an opaque UNKNOWN_ERROR from
        the 401 instead of a re-link prompt.
        """
        for garbage in ("", "not-a-jwt", "aaa.bbb.ccc"):
            with self.subTest(value=garbage):
                self.assertFalse(syftbox_jwt_expired(garbage))


class AccessTokenPropertyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="linked@example.com", password="pw-for-test"
        )

    def _set_tokens(self, access, refresh):
        self.user.syftbox_access_token = access
        self.user.syftbox_refresh_token = refresh
        self.user.save(update_fields=["syftbox_access_token", "syftbox_refresh_token"])

    def test_an_unlinked_identity_raises_invalid_credentials(self):
        self._set_tokens("", "")
        with self.assertRaises(SyftBoxException) as caught:
            self.user.access_token
        self.assertEqual(caught.exception.code, SyftBoxErrorCode.INVALID_CREDENTIALS)
        self.assertEqual(caught.exception.details, {"id": self.user.pk})

    def test_blank_and_whitespace_tokens_count_as_unlinked(self):
        self._set_tokens("   ", "  ")
        with self.assertRaises(SyftBoxException) as caught:
            self.user.access_token
        self.assertEqual(caught.exception.code, SyftBoxErrorCode.INVALID_CREDENTIALS)

    def test_a_valid_access_token_is_returned_without_refreshing(self):
        valid = token(3600)
        self._set_tokens(valid, "refresh-1")
        with mock.patch(REFRESH) as refresh:
            self.assertEqual(self.user.access_token, valid)
        refresh.assert_not_called()

    def test_an_expired_token_triggers_a_refresh(self):
        self._set_tokens(token(-10), "refresh-1")
        with mock.patch(
            REFRESH, return_value=AuthTokens("new-access", "new-refresh")
        ) as refresh:
            self.assertEqual(self.user.access_token, "new-access")
        refresh.assert_called_once_with("refresh-1")

    def test_a_refresh_persists_both_tokens(self):
        """Both are written -- SyftBox rotates the refresh token on use."""
        self._set_tokens(token(-10), "refresh-1")
        with mock.patch(REFRESH, return_value=AuthTokens("new-access", "new-refresh")):
            self.user.access_token

        self.user.refresh_from_db()
        self.assertEqual(self.user.syftbox_access_token, "new-access")
        self.assertEqual(self.user.syftbox_refresh_token, "new-refresh")

    def test_the_refresh_write_is_scoped_to_the_two_token_fields(self):
        """
        `save(update_fields=[...])` -- an unrelated unsaved change on the
        instance is deliberately not persisted by a token refresh.
        """
        self._set_tokens(token(-10), "refresh-1")
        with mock.patch.object(User, "save", autospec=True) as save:
            with mock.patch(
                REFRESH, return_value=AuthTokens("new-access", "new-refresh")
            ):
                self.user.access_token

        self.assertEqual(
            save.call_args.kwargs["update_fields"],
            ["syftbox_access_token", "syftbox_refresh_token"],
        )

    def test_an_empty_access_token_with_a_refresh_token_refreshes(self):
        """Never linked-then-cleared, or a half-written row: refresh recovers it."""
        self._set_tokens("", "refresh-1")
        with mock.patch(
            REFRESH, return_value=AuthTokens("new-access", "new-refresh")
        ) as refresh:
            self.assertEqual(self.user.access_token, "new-access")
        refresh.assert_called_once_with("refresh-1")

    def test_an_expired_token_without_a_refresh_token_raises(self):
        self._set_tokens(token(-10), "")
        with self.assertRaises(SyftBoxException) as caught:
            self.user.access_token
        self.assertEqual(caught.exception.code, SyftBoxErrorCode.TOKEN_EXPIRED)
        self.assertEqual(caught.exception.details, {"id": self.user.pk})

    def test_a_failing_refresh_propagates_and_writes_nothing(self):
        self._set_tokens(token(-10), "refresh-1")
        with mock.patch(
            REFRESH,
            side_effect=SyftBoxException(
                SyftBoxErrorCode.TOKEN_EXPIRED, "refresh rejected"
            ),
        ):
            with self.assertRaises(SyftBoxException) as caught:
                self.user.access_token

        self.assertEqual(caught.exception.code, SyftBoxErrorCode.TOKEN_EXPIRED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.syftbox_refresh_token, "refresh-1")

    def test_a_malformed_access_token_is_returned_rather_than_refreshed(self):
        """
        QUIRK, following from syftbox_jwt_expired. Garbage parses as
        "not expired", so it is handed to the API unchanged and the refresh
        token is never used.
        """
        self._set_tokens("not-a-jwt", "refresh-1")
        with mock.patch(REFRESH) as refresh:
            self.assertEqual(self.user.access_token, "not-a-jwt")
        refresh.assert_not_called()

    def test_every_access_costs_a_decode_and_a_refresh_costs_a_write(self):
        """
        No caching: the property decodes on each call, and each expired read
        performs its own refresh plus save. Worth pinning because the package
        may be tempted to memoise, which would change write behaviour.
        """
        self._set_tokens(token(-10), "refresh-1")
        with mock.patch(
            REFRESH, return_value=AuthTokens(token(3600), "new-refresh")
        ) as refresh:
            self.user.access_token
            self.user.access_token

        self.assertEqual(refresh.call_count, 1)
