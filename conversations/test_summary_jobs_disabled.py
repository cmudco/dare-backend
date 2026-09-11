from io import StringIO
from types import SimpleNamespace
from unittest.mock import call, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from conversations.constants import SenderType
from conversations.management.commands.backfill_conversation_summaries import Command
from conversations.models import Conversation, Message
from conversations.signals import enqueue_conversation_summary_refresh
from conversations.tasks import refresh_conversation_summary_for_conversation


@override_settings(CONVERSATION_SUMMARY_JOBS_ENABLED=False)
class SummaryJobsDisabledTests(SimpleTestCase):
    def test_message_signal_does_not_enqueue_or_access_conversation(self):
        with patch(
            "conversations.signals.refresh_conversation_summary_for_conversation.delay"
        ) as enqueue:
            enqueue_conversation_summary_refresh(
                Message, SimpleNamespace(), created=True
            )
        enqueue.assert_not_called()

    def test_queued_task_skips_database_and_generation(self):
        with patch("conversations.tasks.generate_conversation_summary") as generate:
            result = refresh_conversation_summary_for_conversation(123)
        self.assertEqual(
            result, {"status": "skipped", "reason": "summary_jobs_disabled"}
        )
        generate.assert_not_called()

    def test_backfill_does_not_scan_or_enqueue(self):
        output = StringIO()
        with (
            patch.object(Command, "_find_candidate_pks") as candidates,
            patch(
                "conversations.management.commands.backfill_conversation_summaries."
                "refresh_conversation_summary_for_conversation.delay"
            ) as enqueue,
        ):
            call_command("backfill_conversation_summaries", stdout=output)
        candidates.assert_not_called()
        enqueue.assert_not_called()
        self.assertIn("disabled", output.getvalue())

    @override_settings(CONVERSATION_SUMMARY_JOBS_ENABLED=True)
    def test_signal_can_be_explicitly_reenabled(self):
        message = SimpleNamespace(
            sender_type=SenderType.AI_ASSISTANT,
            conversation=SimpleNamespace(user_id=1),
            conversation_id=123,
        )
        with patch(
            "conversations.signals.refresh_conversation_summary_for_conversation.delay"
        ) as enqueue:
            enqueue_conversation_summary_refresh(Message, message, created=True)
        enqueue.assert_called_once_with(123)

    @override_settings(CONVERSATION_SUMMARY_JOBS_ENABLED=True)
    def test_task_can_be_explicitly_reenabled(self):
        with patch.object(
            Conversation.active_objects, "get", side_effect=Conversation.DoesNotExist
        ) as lookup:
            result = refresh_conversation_summary_for_conversation(123)
        lookup.assert_called_once_with(pk=123)
        self.assertEqual(result["reason"], "conversation_not_found")

    @override_settings(CONVERSATION_SUMMARY_JOBS_ENABLED=True)
    def test_backfill_can_be_explicitly_reenabled(self):
        with (
            patch.object(Command, "_find_candidate_pks", return_value=[123, 456]),
            patch(
                "conversations.management.commands.backfill_conversation_summaries."
                "refresh_conversation_summary_for_conversation.delay"
            ) as enqueue,
        ):
            call_command("backfill_conversation_summaries", stdout=StringIO())
        self.assertEqual(enqueue.call_args_list, [call(123), call(456)])
