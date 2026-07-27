from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from feature_flags.models import FeatureFlag
from research.constants import AgentRunStatus, ResearchSessionMode
from research.models import (
    ResearchAgentRun,
    ResearchArtifact,
    ResearchChatMessage,
    ResearchProject,
    ResearchSession,
    ResearchStagingItem,
)
from research.services.hermes_service import HermesStopResult
from research.tasks import run_artifact_job, run_critic_job, run_scout_job
from users.constants import RoleChoice

User = get_user_model()


class ResearchCancellationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="cancel-owner@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.other = User.objects.create_user(
            email="cancel-other@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.project = ResearchProject.objects.create(user=self.user, title="R2")
        self.session = ResearchSession.objects.create(
            project=self.project,
            user=self.user,
            mode=ResearchSessionMode.SCOUT,
            hermes_session_id="r2-session",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.test_atomic_depth = len(transaction.get_connection().atomic_blocks)

    def make_run(self, **overrides):
        values = {
            "session": self.session,
            "project": self.project,
            "user": self.user,
            "role": "scout",
            "mode": ResearchSessionMode.SCOUT,
            "task": "research",
            "status": AgentRunStatus.RUNNING,
            "status_detail": "Working…",
            "started_at": timezone.now(),
            "hermes_run_id": "run_r2",
        }
        values.update(overrides)
        return ResearchAgentRun.objects.create(**values)

    @staticmethod
    def acknowledged_stop():
        return HermesStopResult(
            code="stop_acknowledged",
            acknowledged=True,
            http_status=200,
            upstream_status="stopping",
        )

    def test_intent_is_committed_before_network_and_cancel_is_confirmed(self):
        run = self.make_run()
        hermes = MagicMock()

        def stop_after_commit(_run_id):
            persisted = ResearchAgentRun.objects.get(id=run.id)
            self.assertIsNotNone(persisted.cancellation_requested_at)
            self.assertEqual(persisted.cancellation_requested_by, self.user)
            self.assertEqual(
                len(transaction.get_connection().atomic_blocks), self.test_atomic_depth
            )
            return self.acknowledged_stop()

        hermes.stop_run.side_effect = stop_after_commit
        hermes.get_run.return_value = {
            "status": "cancelled",
            "usage": {"total_tokens": 12},
        }

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 202)
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertIsNotNone(run.cancellation_acknowledged_at)
        self.assertIsNotNone(run.cancellation_confirmed_at)
        self.assertEqual(run.cancellation_attempt_count, 1)
        self.assertEqual(run.usage, {"total_tokens": 12})
        self.assertEqual(response.data["cancellation"]["state"], "confirmed")

    def test_completed_terminal_get_wins_cancellation_race(self):
        run = self.make_run()
        hermes = MagicMock()
        hermes.stop_run.return_value = HermesStopResult(
            code="hermes_run_not_found",
            http_status=404,
            detail="Hermes did not have an active stop target for this run.",
        )
        hermes.get_run.return_value = {
            "status": "completed",
            "output": "done",
            "usage": {"total_tokens": 8},
        }

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 202)
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.COMPLETED)
        self.assertIsNotNone(run.completed_at)
        self.assertIsNone(run.cancellation_confirmed_at)
        hermes.get_run.assert_called_once_with("run_r2")

    def test_existing_terminal_run_never_calls_hermes(self):
        run = self.make_run(
            status=AgentRunStatus.COMPLETED, completed_at=timezone.now()
        )

        with patch(
            "research.services.cancellation_service.get_hermes_service"
        ) as get_hermes:
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 200)
        get_hermes.assert_not_called()
        run.refresh_from_db()
        self.assertIsNone(run.cancellation_requested_at)

    def test_404_then_unavailable_terminal_truth_is_unknown_not_cancelled(self):
        run = self.make_run()
        response_404 = MagicMock(status_code=404)
        error = requests.HTTPError(response=response_404)
        hermes = MagicMock()
        hermes.stop_run.return_value = HermesStopResult(
            code="hermes_run_not_found",
            http_status=404,
            detail="Hermes did not have an active stop target for this run.",
        )
        hermes.get_run.side_effect = error

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.OUTCOME_UNKNOWN)
        self.assertIsNone(run.completed_at)
        self.assertIsNone(run.cancellation_confirmed_at)
        self.assertEqual(run.cancellation_stop_http_status, 404)
        self.assertEqual(run.cancellation_error_code, "hermes_run_not_found")

    def test_timeout_with_active_terminal_get_remains_active_and_unconfirmed(self):
        for terminal_status in (
            AgentRunStatus.RUNNING,
            AgentRunStatus.WAITING_FOR_APPROVAL,
            AgentRunStatus.STOPPING,
        ):
            with self.subTest(terminal_status=terminal_status):
                run = self.make_run(hermes_run_id=f"run_{terminal_status}")
                hermes = MagicMock()
                hermes.stop_run.return_value = HermesStopResult(
                    code="stop_timeout",
                    detail="The Hermes stop request timed out.",
                )
                hermes.get_run.return_value = {"status": terminal_status}

                with patch(
                    "research.services.cancellation_service.get_hermes_service",
                    return_value=hermes,
                ):
                    self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

                run.refresh_from_db()
                self.assertEqual(run.status, terminal_status)
                self.assertIsNone(run.completed_at)
                self.assertIsNone(run.cancellation_acknowledged_at)
                self.assertIsNone(run.cancellation_confirmed_at)
                self.assertEqual(run.cancellation_error_code, "stop_timeout")

    def test_repeated_requests_share_one_intent_and_attempt_lease(self):
        run = self.make_run()
        hermes = MagicMock()
        hermes.stop_run.return_value = self.acknowledged_stop()
        hermes.get_run.return_value = {"status": "running"}

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            first = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")
            second = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        run.refresh_from_db()
        self.assertEqual(run.cancellation_attempt_count, 1)
        hermes.stop_run.assert_called_once_with("run_r2")
        hermes.get_run.assert_called_once_with("run_r2")

    def test_request_arriving_during_network_attempt_does_not_start_parallel_stop(self):
        run = self.make_run()
        hermes = MagicMock()
        nested_statuses = []

        def stop_with_overlapping_request(_run_id):
            overlapping_client = APIClient()
            overlapping_client.force_authenticate(self.user)
            nested = overlapping_client.post(
                f"/api/research/agent-runs/{run.id}/cancel/"
            )
            nested_statuses.append(nested.status_code)
            return self.acknowledged_stop()

        hermes.stop_run.side_effect = stop_with_overlapping_request
        hermes.get_run.return_value = {"status": "running"}

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(nested_statuses, [202])
        run.refresh_from_db()
        self.assertEqual(run.cancellation_attempt_count, 1)
        hermes.stop_run.assert_called_once_with("run_r2")
        hermes.get_run.assert_called_once_with("run_r2")

    def test_non_owner_cannot_cancel_or_discover_run(self):
        run = self.make_run()
        self.client.force_authenticate(self.other)

        with patch(
            "research.services.cancellation_service.get_hermes_service"
        ) as get_hermes:
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 404)
        get_hermes.assert_not_called()

    def test_disallowed_role_cannot_cancel(self):
        run = self.make_run()
        self.user.platform_role = RoleChoice.USER
        self.user.save(update_fields=["platform_role"])

        with patch(
            "research.services.cancellation_service.get_hermes_service"
        ) as get_hermes:
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        self.assertEqual(response.status_code, 403)
        get_hermes.assert_not_called()

    def test_safe_cancellation_metadata_excludes_requester_and_raw_response(self):
        run = self.make_run()
        hermes = MagicMock()
        hermes.stop_run.return_value = HermesStopResult(
            code="stop_upstream_error",
            http_status=503,
            detail="Hermes returned a server error for the stop request.",
        )
        hermes.get_run.side_effect = requests.ConnectionError(
            "Bearer secret-token unsafe-body"
        )

        with patch(
            "research.services.cancellation_service.get_hermes_service",
            return_value=hermes,
        ):
            response = self.client.post(f"/api/research/agent-runs/{run.id}/cancel/")

        serialized = str(response.data)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("unsafe-body", serialized)
        self.assertNotIn("requested_by", response.data["cancellation"])
        run.refresh_from_db()
        self.assertNotIn("secret-token", run.cancellation_error_detail)

    def test_queued_dare_job_honors_intent_before_starting_hermes(self):
        run = self.make_run(hermes_run_id="", cancellation_requested_at=timezone.now())

        with patch("research.tasks.get_hermes_service") as get_hermes:
            run_scout_job(run.id)

        get_hermes.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertIsNone(run.cancellation_confirmed_at)
        self.assertEqual(ResearchStagingItem.objects.filter(run=run).count(), 0)

    def test_cancelled_scout_output_creates_no_findings(self):
        run = self.make_run(hermes_run_id="")
        hermes = MagicMock()
        hermes.start_run.return_value = {
            "run_id": "scout_cancelled",
            "status": "started",
        }
        hermes.stream_events.return_value = iter(
            [
                {"event": "message.delta", "delta": '{"stagingItems":['},
                {"event": "run.cancelled"},
            ]
        )
        hermes.get_run.return_value = {"status": "cancelled"}

        with (
            patch("research.tasks.get_hermes_service", return_value=hermes),
            patch("research.tasks.gather_tool_results", return_value=[]),
            patch("research.tasks.parse_staging_items") as parse,
        ):
            run_scout_job(run.id)

        parse.assert_not_called()
        self.assertEqual(ResearchStagingItem.objects.filter(run=run).count(), 0)

    def test_cancelled_critic_output_creates_no_verdict(self):
        run = self.make_run(role="critic", hermes_run_id="")
        item = ResearchStagingItem.objects.create(
            project=self.project, run=run, title="Candidate"
        )
        hermes = MagicMock()
        hermes.start_run.return_value = {
            "run_id": "critic_cancelled",
            "status": "started",
        }
        hermes.stream_events.return_value = iter([{"event": "run.cancelled"}])
        hermes.get_run.return_value = {"status": "cancelled"}

        with (
            patch("research.tasks.get_hermes_service", return_value=hermes),
            patch("research.tasks.parse_critic_verdict") as parse,
        ):
            run_critic_job(run.id, item.id)

        parse.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.critic_metadata, {})

    def test_cancelled_artifact_output_creates_no_artifact(self):
        run = self.make_run(role="presenter", hermes_run_id="")
        hermes = MagicMock()
        hermes.start_run.return_value = {
            "run_id": "artifact_cancelled",
            "status": "started",
        }
        hermes.stream_events.return_value = iter(
            [
                {"event": "message.delta", "delta": '{"artifacts":['},
                {"event": "run.cancelled"},
            ]
        )
        hermes.get_run.return_value = {"status": "cancelled"}

        with (
            patch("research.tasks.get_hermes_service", return_value=hermes),
            patch("research.tasks.parse_artifacts") as parse,
        ):
            run_artifact_job(run.id)

        parse.assert_not_called()
        self.assertEqual(ResearchArtifact.objects.filter(run=run).count(), 0)


class ResearchChatTerminalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="chat-terminal@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.project = ResearchProject.objects.create(user=self.user, title="Chat R2")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_cancelled_chat_does_not_persist_successful_assistant_message(self):
        hermes = MagicMock()
        hermes.start_run.return_value = {
            "run_id": "chat_cancelled",
            "status": "started",
        }
        hermes.stream_events.return_value = iter(
            [
                {"event": "message.delta", "delta": "partial"},
                {"event": "run.cancelled"},
            ]
        )
        hermes.get_run.return_value = {"status": "cancelled"}

        with patch("research.api.views.get_hermes_service", return_value=hermes):
            response = self.client.post(
                f"/api/research/projects/{self.project.id}/chat/",
                {"message": "hello"},
                format="json",
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("cancelled", body)
        run = ResearchAgentRun.objects.get(hermes_run_id="chat_cancelled")
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertEqual(
            ResearchChatMessage.objects.filter(run=run, role="assistant").count(), 0
        )

    def test_failed_chat_does_not_persist_successful_assistant_message(self):
        hermes = MagicMock()
        hermes.start_run.return_value = {"run_id": "chat_failed", "status": "started"}
        hermes.stream_events.return_value = iter([{"event": "run.failed"}])
        hermes.get_run.return_value = {"status": "failed", "error": "unsafe detail"}

        with patch("research.api.views.get_hermes_service", return_value=hermes):
            response = self.client.post(
                f"/api/research/projects/{self.project.id}/chat/",
                {"message": "hello"},
                format="json",
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode()

        self.assertIn("failed", body)
        run = ResearchAgentRun.objects.get(hermes_run_id="chat_failed")
        self.assertEqual(run.status, AgentRunStatus.FAILED)
        self.assertNotIn("unsafe detail", run.error)
        self.assertEqual(
            ResearchChatMessage.objects.filter(run=run, role="assistant").count(), 0
        )
