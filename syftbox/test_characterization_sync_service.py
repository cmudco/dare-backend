"""
Characterization tests for SyftBoxSyncService.

Phase 2 turns this into a pure `diff()` that returns new/changed/deleted and
writes nothing. These tests pin the *decisions* -- which action is chosen for
each remote-vs-DB combination, and how the counters add up -- so the inverted
version can be proved equivalent before DARE's call sites change.

Persistence is stubbed deliberately. What matters here is the routing, not what
_create_ref_and_enqueue does once it is called; that stays in DARE either way.
"""

from unittest import mock

from django.test import SimpleTestCase

from syftbox.dtos import RemoteSyftBoxFile
from syftbox.services.syftbox_sync_service import SyftBoxSyncService

SERVICE = "syftbox.services.syftbox_sync_service"


def db_file(path, etag=None):
    """A stand-in File row: sync only touches .file.name, .syftbox_etag, .delete()."""
    row = mock.Mock()
    row.file.name = path
    row.syftbox_etag = etag
    return row


def remote(path, etag=None, size=None):
    return RemoteSyftBoxFile(
        path=path, name=path.rsplit("/", 1)[-1], size=size, etag=etag
    )


class SyncRoutingTests(SimpleTestCase):
    """Each remote/DB combination routes to exactly one outcome."""

    def setUp(self):
        self.user = mock.Mock(pk=1)
        patcher = mock.patch(f"{SERVICE}.File")
        self.File = patcher.start()
        self.addCleanup(patcher.stop)
        self.service = SyftBoxSyncService()

    def _run(self, remote_files, existing=()):
        self.File.active_objects.filter.return_value = list(existing)
        with mock.patch.object(SyftBoxSyncService, "_create_ref_and_enqueue") as create:
            with mock.patch.object(
                SyftBoxSyncService, "_refresh_changed_file"
            ) as refresh:
                with mock.patch.object(
                    SyftBoxSyncService, "_update_existing_metadata"
                ) as update:
                    result = self.service.sync(
                        user=self.user, remote_files=list(remote_files)
                    )
        return result, create, refresh, update

    def test_a_new_remote_file_is_created(self):
        result, create, refresh, update = self._run([remote("files/a.pdf")])
        self.assertEqual(
            (result.created, result.updated, result.kept, result.deleted), (1, 0, 0, 0)
        )
        create.assert_called_once()
        refresh.assert_not_called()
        update.assert_not_called()

    def test_a_known_file_with_a_changed_etag_is_refreshed(self):
        result, create, refresh, update = self._run(
            [remote("files/a.pdf", etag="new")], [db_file("a.pdf", etag="old")]
        )
        self.assertEqual((result.created, result.updated, result.kept), (0, 1, 0))
        refresh.assert_called_once()
        create.assert_not_called()

    def test_a_known_file_with_an_unchanged_etag_is_kept(self):
        result, create, refresh, update = self._run(
            [remote("files/a.pdf", etag="same")], [db_file("a.pdf", etag="same")]
        )
        self.assertEqual((result.created, result.updated, result.kept), (0, 0, 1))
        update.assert_called_once()
        refresh.assert_not_called()

    def test_a_known_file_whose_db_etag_is_missing_is_only_kept(self):
        """
        QUIRK, following from has_remote_etag_change requiring both sides.
        A row with no stored etag is never refreshed, so replaced remote
        content goes unnoticed -- it is counted as kept, not updated.
        """
        result, create, refresh, update = self._run(
            [remote("files/a.pdf", etag="new")], [db_file("a.pdf", etag=None)]
        )
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.kept, 1)
        refresh.assert_not_called()

    def test_a_db_file_absent_from_the_remote_listing_is_deleted(self):
        row = db_file("gone.pdf")
        result, *_ = self._run([], [row])
        self.assertEqual(result.deleted, 1)
        row.delete.assert_called_once()

    def test_acl_files_are_skipped_and_counted_as_kept(self):
        result, create, *_ = self._run([remote("files/syft.pub.yaml")])
        self.assertEqual((result.created, result.kept), (0, 1))
        create.assert_not_called()

    def test_an_acl_file_does_not_keep_a_db_row_alive(self):
        """
        _collect_remote_paths drops ACL entries, so a DB row named
        syft.pub.yaml would be deleted even though the remote still has it.
        """
        row = db_file("syft.pub.yaml")
        result, *_ = self._run([remote("files/syft.pub.yaml")], [row])
        self.assertEqual(result.deleted, 1)

    def test_paths_are_matched_after_normalisation(self):
        """A full blob key and a stored relative path are the same file."""
        result, create, refresh, update = self._run(
            [remote("u@e.com/app_data/dare/files/a.pdf", etag="same")],
            [db_file("a.pdf", etag="same")],
        )
        self.assertEqual(result.kept, 1)
        self.assertEqual(result.created, 0)


class CountersAndIsolationTests(SimpleTestCase):
    def setUp(self):
        self.user = mock.Mock(pk=1)
        patcher = mock.patch(f"{SERVICE}.File")
        self.File = patcher.start()
        self.addCleanup(patcher.stop)
        self.service = SyftBoxSyncService()

    def _run(self, remote_files, existing=(), create_side_effect=None):
        self.File.active_objects.filter.return_value = list(existing)
        with mock.patch.object(
            SyftBoxSyncService,
            "_create_ref_and_enqueue",
            side_effect=create_side_effect,
        ):
            with mock.patch.object(SyftBoxSyncService, "_refresh_changed_file"):
                with mock.patch.object(SyftBoxSyncService, "_update_existing_metadata"):
                    return self.service.sync(
                        user=self.user, remote_files=list(remote_files)
                    )

    def test_total_remote_counts_acl_files_too(self):
        result = self._run([remote("files/a.pdf"), remote("files/syft.pub.yaml")])
        self.assertEqual(result.total_remote, 2)

    def test_only_this_users_syftbox_rows_are_considered(self):
        self._run([])
        kwargs = self.File.active_objects.filter.call_args.kwargs
        self.assertIs(kwargs["user"], self.user)
        self.assertIn("storage_backend", kwargs)

    def test_one_failure_does_not_stop_the_rest(self):
        result = self._run(
            [remote("files/a.pdf"), remote("files/b.pdf")],
            create_side_effect=[RuntimeError("upload gone"), None],
        )
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("a.pdf", result.errors[0])

    def test_a_delete_failure_is_recorded_and_the_loop_continues(self):
        bad, good = db_file("bad.pdf"), db_file("good.pdf")
        bad.delete.side_effect = RuntimeError("locked")
        result = self._run([], [bad, good])
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.deleted, 1)

    def test_an_empty_remote_listing_with_no_db_rows_is_a_no_op(self):
        result = self._run([])
        self.assertEqual(
            (
                result.total_remote,
                result.created,
                result.updated,
                result.kept,
                result.deleted,
                result.failed,
            ),
            (0, 0, 0, 0, 0, 0),
        )

    def test_creates_updates_and_deletes_can_happen_in_one_run(self):
        result = self._run(
            [remote("files/new.pdf"), remote("files/changed.pdf", etag="new")],
            [db_file("changed.pdf", etag="old"), db_file("gone.pdf")],
        )
        self.assertEqual((result.created, result.updated, result.deleted), (1, 1, 1))
