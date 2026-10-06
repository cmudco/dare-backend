"""
The SyftBox credential backfill must not lose or mangle anything.

`users.0038` copies tokens off `users.User` and `core.DareConfig` into the
package's `SyftBoxAccount` table. It runs against live credentials on deploy,
so this builds the schema as it was *before* that migration, writes rows the
old way, migrates forward, and checks every credential arrived intact.

Without this the backfill is only ever exercised against an empty database.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SyftBoxBackfillMigrationTests(TransactionTestCase):
    """TransactionTestCase because migrations run outside the test transaction."""

    app = "users"
    before = "0037_accesscodegroup_provisioned_by"
    after = "0038_backfill_syftbox_accounts"

    def _migrate(self, target):
        """
        Roll users to `target`. 0038 changes no schema, so the real models are
        identical to the historical ones -- only whether it has *run* differs.
        """
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(self.app, target)])
        executor.loader.build_graph()

    def tearDown(self):
        # Leave the database on the latest schema for whatever runs next.
        self._migrate(self.after)

    def _seed(self):
        from core.models import DareConfig
        from django.contrib.auth import get_user_model

        User = get_user_model()

        linked = User.objects.create(
            email="linked@example.com",
            password="x",
            syftbox_access_token="user-access",
            syftbox_refresh_token="user-refresh",
        )
        User.objects.create(email="never-linked@example.com", password="x")
        User.objects.create(
            email="blank@example.com",
            password="x",
            syftbox_access_token="",
            syftbox_refresh_token="",
        )
        # active_objects, not objects: DareConfig declares no default manager.
        config = DareConfig.active_objects.create(
            project_email="shared@example.com",
            syftbox_access_token="config-access",
            syftbox_refresh_token="config-refresh",
        )
        return linked, config

    def test_credentials_survive_the_migration(self):
        self._migrate(self.before)
        linked, _config = self._seed()

        self._migrate(self.after)
        from syftbox_connect.models import SyftBoxAccount

        account = SyftBoxAccount.objects.get(email="linked@example.com")
        self.assertEqual(account.syftbox_access_token, "user-access")
        self.assertEqual(account.syftbox_refresh_token, "user-refresh")
        self.assertEqual(account.owner_id, linked.pk)

    def test_a_project_level_identity_is_carried_over_unowned(self):
        self._migrate(self.before)
        self._seed()

        self._migrate(self.after)
        from syftbox_connect.models import SyftBoxAccount

        account = SyftBoxAccount.objects.get(email="shared@example.com")
        self.assertEqual(account.syftbox_access_token, "config-access")
        self.assertIsNone(account.owner_id)

    def test_users_without_credentials_are_not_given_an_account(self):
        self._migrate(self.before)
        self._seed()

        self._migrate(self.after)
        from syftbox_connect.models import SyftBoxAccount

        for email in ("never-linked@example.com", "blank@example.com"):
            with self.subTest(email=email):
                self.assertFalse(SyftBoxAccount.objects.filter(email=email).exists())

    def test_the_originals_are_left_untouched(self):
        """
        Expand, not move. Nothing reads the new table exclusively yet, so the
        columns must still hold what they held.
        """
        self._migrate(self.before)
        self._seed()

        self._migrate(self.after)
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.get(email="linked@example.com")
        self.assertEqual(user.syftbox_access_token, "user-access")
        self.assertEqual(user.syftbox_refresh_token, "user-refresh")

    def test_running_it_twice_creates_no_duplicates(self):
        """Deploys re-run migrations; a second pass must be a no-op."""
        self._migrate(self.before)
        self._seed()
        self._migrate(self.after)

        from syftbox_connect.models import SyftBoxAccount

        first = SyftBoxAccount.objects.count()

        self._migrate(self.before)
        self._migrate(self.after)
        self.assertEqual(SyftBoxAccount.objects.count(), first)

    def test_unapplying_removes_only_what_it_created(self):
        self._migrate(self.before)
        self._seed()
        self._migrate(self.after)

        from syftbox_connect.dtos import AuthTokens
        from syftbox_connect.models import SyftBoxAccount

        # Someone links *after* the migration ran; that is real data.
        SyftBoxAccount.link(
            email="later@example.com", tokens=AuthTokens("new-access", "new-refresh")
        )

        self._migrate(self.before)
        self.assertFalse(
            SyftBoxAccount.objects.filter(email="linked@example.com").exists()
        )
        self.assertTrue(
            SyftBoxAccount.objects.filter(email="later@example.com").exists()
        )
