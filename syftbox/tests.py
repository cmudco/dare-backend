from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from syftbox.scheduler import SyftBoxDatasiteScheduler
from syftbox.tasks import sync_syftbox_datasites, sync_user_datasite


@override_settings(SYFTBOX={"ENABLED": False})
class DisabledSyftBoxSyncTests(SimpleTestCase):
    @patch("syftbox.tasks.syftbox_datasite_sync_service.SyftBoxDatasitePollService")
    @patch("syftbox.tasks.get_user_model")
    def test_stale_user_job_exits_without_loading_user_or_service(
        self,
        get_user_model,
        poll_service,
    ):
        result = sync_user_datasite(41)

        self.assertEqual(
            result,
            {
                "status": "skipped",
                "user_id": 41,
                "reason": "syftbox_disabled",
            },
        )
        get_user_model.assert_not_called()
        poll_service.assert_not_called()

    @patch("syftbox.tasks.enqueue")
    @patch("syftbox.tasks.get_user_model")
    def test_dispatcher_exits_without_querying_or_enqueuing(
        self,
        get_user_model,
        enqueue,
    ):
        result = sync_syftbox_datasites()

        self.assertEqual(
            result,
            {
                "status": "skipped",
                "reason": "syftbox_disabled",
                "eligible_users": 0,
                "enqueued_jobs": 0,
            },
        )
        get_user_model.assert_not_called()
        enqueue.assert_not_called()

    @patch("syftbox.scheduler.get_scheduler")
    def test_start_cancels_existing_recurring_schedule(self, get_scheduler):
        scheduler = get_scheduler.return_value

        result = SyftBoxDatasiteScheduler().start()

        scheduler.cancel.assert_called_once_with("syftbox_datasite_sync")
        scheduler.schedule.assert_not_called()
        self.assertEqual(result["status"], "disabled")

    @patch("syftbox.scheduler.get_scheduler")
    def test_run_now_does_not_schedule_a_job(self, get_scheduler):
        scheduler = get_scheduler.return_value

        result = SyftBoxDatasiteScheduler().run_now()

        scheduler.schedule.assert_not_called()
        self.assertEqual(
            result,
            {"status": "disabled", "reason": "syftbox_disabled"},
        )


@override_settings(SYFTBOX={"ENABLED": True})
class EnabledSyftBoxSyncTests(SimpleTestCase):
    @patch("syftbox.tasks.syftbox_datasite_sync_service.SyftBoxDatasitePollService")
    @patch("syftbox.tasks.get_user_model")
    def test_user_job_still_runs_when_enabled(self, get_user_model, poll_service):
        user = SimpleNamespace(id=41)
        get_user_model.return_value.active_objects.get.return_value = user
        poll_service.return_value.sync_user_datasite.return_value = SimpleNamespace(
            total_remote=3,
            created=1,
            updated=1,
            kept=1,
            deleted=0,
            failed=0,
            errors=[],
        )

        result = sync_user_datasite(user.id)

        poll_service.return_value.sync_user_datasite.assert_called_once_with(user)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_remote"], 3)

    @patch("syftbox.tasks.enqueue")
    @patch("syftbox.tasks.get_user_model")
    def test_dispatcher_still_enqueues_eligible_users(
        self,
        get_user_model,
        enqueue,
    ):
        users = Mock()
        users.iterator.return_value = [SimpleNamespace(id=41)]
        users.count.return_value = 1
        (
            get_user_model.return_value.active_objects.filter.return_value.exclude.return_value.only.return_value
        ) = users

        result = sync_syftbox_datasites()

        enqueue.assert_called_once_with(sync_user_datasite, 41)
        self.assertEqual(
            result,
            {"status": "ok", "eligible_users": 1, "enqueued_jobs": 1},
        )
