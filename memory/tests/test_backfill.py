"""Historical conversation backfill contract and queue ordering."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from memory.constants import MemoryBackfillStatus
from memory.models import MemoryBackfillRun, MemoryLedgerEntry, MemoryRecord
from memory.services.backfill import eligible_messages
from memory.tasks import (
    queue_memory_backfill,
    run_memory_backfill_turn,
    run_memory_writer,
)


class MemoryBackfillTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="memory-backfill@example.com", password="x"
        )
        cls.other = get_user_model().objects.create_user(
            email="memory-backfill-other@example.com", password="x"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def add_turn(self, user, suffix, *, processed=False, at=None):
        conversation = Conversation.active_objects.create(
            user=user,
            conversation_id=f"backfill-{user.id}-{suffix}",
        )
        with patch(
            "conversations.signals.refresh_conversation_summary_for_conversation.delay"
        ):
            user_message = Message.active_objects.create(
                conversation=conversation,
                sender_type=SenderType.PLAYER,
                message=f"I prefer concise answer {suffix}.",
            )
            reply = Message.active_objects.create(
                conversation=conversation,
                sender_type=SenderType.AI_ASSISTANT,
                message=f"Understood {suffix}.",
                memory_write_data={} if processed else None,
            )
        if at is not None:
            Message.active_objects.filter(pk__in=[user_message.pk, reply.pk]).update(
                created_at=at
            )
            reply.refresh_from_db()
        return reply


class MemoryBackfillApiTests(MemoryBackfillTestCase):
    def test_start_is_user_scoped_and_idempotent_while_active(self):
        first = self.add_turn(self.user, "first")
        second = self.add_turn(self.user, "second")
        processed = self.add_turn(self.user, "already-processed", processed=True)
        self.add_turn(self.other, "private")

        with patch("memory.services.backfill.get_queue") as get_queue:
            response = self.client.post("/api/memory/v2/backfill/", format="json")
            repeated = self.client.post("/api/memory/v2/backfill/", format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(repeated.status_code, 200)
        payload = response.json()["run"]
        self.assertEqual(payload["status"], MemoryBackfillStatus.QUEUED)
        self.assertEqual(payload["totalTurns"], 2)
        self.assertEqual(payload["processedTurns"], 0)
        self.assertEqual(payload["id"], repeated.json()["run"]["id"])

        run = MemoryBackfillRun.objects.get(pk=payload["id"])
        self.assertEqual(run.cutoff_message_id, processed.id)
        self.assertGreater(run.cutoff_message_id, first.id)
        self.assertGreater(processed.id, second.id)
        self.assertEqual(get_queue.return_value.enqueue.call_count, 1)

    def test_an_empty_history_completes_without_touching_the_queue(self):
        with patch("memory.services.backfill.get_queue") as get_queue:
            response = self.client.post("/api/memory/v2/backfill/", format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run"]["status"], "completed")
        self.assertEqual(response.json()["run"]["totalTurns"], 0)
        get_queue.assert_not_called()

    def test_start_filters_chat_turns_to_the_inclusive_date_range(self):
        self.add_turn(
            self.user,
            "before",
            at=datetime(2025, 12, 31, 12, tzinfo=UTC),
        )
        included = self.add_turn(
            self.user,
            "included",
            at=datetime(2026, 1, 15, 12, tzinfo=UTC),
        )
        self.add_turn(
            self.user,
            "after",
            at=datetime(2026, 2, 1, 12, tzinfo=UTC),
        )

        with patch("memory.services.backfill.get_queue"):
            response = self.client.post(
                "/api/memory/v2/backfill/",
                {"since": "2026-01-01", "until": "2026-01-31"},
                format="json",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.json()["run"]
        self.assertEqual(payload["totalTurns"], 1)
        self.assertEqual(payload["since"], "2026-01-01")
        self.assertEqual(payload["until"], "2026-01-31")
        run = MemoryBackfillRun.objects.get(pk=payload["id"])
        self.assertEqual(
            list(eligible_messages(run).values_list("id", flat=True)), [included.id]
        )

    def test_rejects_an_inverted_date_range(self):
        response = self.client.post(
            "/api/memory/v2/backfill/",
            {"since": "2026-02-01", "until": "2026-01-01"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("until", response.json())

    def test_stop_marks_the_active_run_and_is_idempotent(self):
        run = MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.RUNNING,
            total_turns=10,
            processed_turns=3,
        )

        response = self.client.delete("/api/memory/v2/backfill/")
        repeated = self.client.delete("/api/memory/v2/backfill/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "stopped")
        self.assertEqual(repeated.json()["run"]["id"], str(run.id))
        run.refresh_from_db()
        self.assertEqual(run.status, MemoryBackfillStatus.STOPPED)
        self.assertIsNotNone(run.completed_at)

    def test_status_only_returns_the_authenticated_users_run(self):
        MemoryBackfillRun.objects.create(
            user=self.other,
            status=MemoryBackfillStatus.COMPLETED,
        )
        self.assertIsNone(self.client.get("/api/memory/v2/backfill/").json()["run"])

    def test_memory_cannot_be_cleared_mid_build(self):
        MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="location",
            text="Lives in Lahore.",
        )
        MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.RUNNING,
        )

        response = self.client.delete("/api/memory/clear/")

        self.assertEqual(response.status_code, 409)
        self.assertIn("finish", response.json()["detail"])
        self.assertTrue(MemoryRecord.objects.filter(user=self.user).exists())


class MemoryBackfillTaskTests(MemoryBackfillTestCase):
    def test_bootstrap_queues_every_reply_in_chronological_order(self):
        first = self.add_turn(self.user, "first")
        second = self.add_turn(self.user, "second")
        run = MemoryBackfillRun.objects.create(
            user=self.user,
            cutoff_message_id=second.id,
            total_turns=2,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.run_memory_backfill_turn.delay"
        ) as delay:
            queue_memory_backfill(str(run.id))

        self.assertEqual(
            [call.args[1] for call in delay.call_args_list],
            [first.id, second.id],
        )
        run.refresh_from_db()
        self.assertEqual(run.status, MemoryBackfillStatus.RUNNING)
        self.assertEqual(run.total_turns, 2)
        self.assertIsNotNone(run.started_at)

    def test_turn_uses_the_live_writer_and_completes_the_run(self):
        reply = self.add_turn(self.user, "only")
        run = MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.RUNNING,
            cutoff_message_id=reply.id,
            total_turns=1,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.run_memory_writer"
        ) as writer:
            run_memory_backfill_turn(str(run.id), reply.id)

        writer.assert_called_once_with(reply.id, backfill_run_id=str(run.id))
        run.refresh_from_db()
        self.assertEqual(run.status, MemoryBackfillStatus.COMPLETED)
        self.assertEqual(run.processed_turns, 1)
        self.assertIsNotNone(run.completed_at)

    def test_a_writer_failure_is_visible_and_retryable(self):
        reply = self.add_turn(self.user, "failure")
        run = MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.RUNNING,
            cutoff_message_id=reply.id,
            total_turns=1,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.run_memory_writer", side_effect=RuntimeError("provider down")
        ), self.assertRaises(RuntimeError):
            run_memory_backfill_turn(str(run.id), reply.id)

        run.refresh_from_db()
        self.assertEqual(run.status, MemoryBackfillStatus.FAILED)
        self.assertIn("Try again", run.error_message)

    def test_a_stopped_run_makes_already_queued_turns_no_ops(self):
        reply = self.add_turn(self.user, "stopped")
        run = MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.STOPPED,
            cutoff_message_id=reply.id,
            total_turns=1,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.run_memory_writer"
        ) as writer:
            run_memory_backfill_turn(str(run.id), reply.id)

        writer.assert_not_called()

    def test_live_turn_waits_behind_an_active_historical_build(self):
        reply = self.add_turn(self.user, "live")
        MemoryBackfillRun.objects.create(
            user=self.user,
            status=MemoryBackfillStatus.RUNNING,
            cutoff_message_id=reply.id - 1,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.USE_POSTGRES", True
        ), patch("memory.tasks.run_memory_writer.delay") as delay, patch(
            "memory.tasks.ingest_turn"
        ) as ingest:
            run_memory_writer(reply.id)

        delay.assert_called_once_with(reply.id)
        ingest.assert_not_called()

    def test_existing_ledger_restores_a_missing_reply_marker(self):
        reply = self.add_turn(self.user, "ledger-marker")
        user_message = Message.active_objects.get(
            conversation=reply.conversation,
            sender_type=SenderType.PLAYER,
        )
        MemoryLedgerEntry.objects.create(
            user=self.user,
            action="ignore",
            proposed_action="ignore",
            source_message=user_message,
        )

        with patch("memory.tasks.close_old_connections"), patch(
            "memory.tasks.USE_POSTGRES", True
        ), patch("memory.tasks.ingest_turn") as ingest:
            run_memory_writer(reply.id, backfill_run_id="historical-run")

        ingest.assert_not_called()
        reply.refresh_from_db()
        self.assertEqual(reply.memory_write_data["skipped"], "turn already ingested")
