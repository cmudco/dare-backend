from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.message_helpers.learning_progress_helpers import (
    run_learning_progress_stream,
)


class ProgressSocketContractTests(SimpleTestCase):
    def setUp(self):
        self.conversation = SimpleNamespace(id=5569, conversation_id="OQ48N")
        self.send = AsyncMock()

    def test_live_progress_uses_subscription_id_and_persists_assessment(self):
        async def chunks(**kwargs):
            yield "## Progress Report", None
            yield "\n\n- ✅ Emotion", {"input_tokens": 2370, "output_tokens": 183}

        assessment = SimpleNamespace(id=7)
        service = SimpleNamespace(
            assess_learning_progress=chunks,
            _save_progress_assessment=AsyncMock(return_value=assessment),
        )
        message = SimpleNamespace(id=42241)
        llm = SimpleNamespace(id=31)
        with patch(
            "conversations.services.message_helpers.learning_progress_helpers."
            "update_message_learning_progress",
            new_callable=AsyncMock,
        ) as update_message:
            async_to_sync(run_learning_progress_stream)(
                conversation=self.conversation,
                message_data={
                    "enable_progress": True,
                    "progress_llm_id": "31",
                    "bot_meta": {
                        "learning_goals": "Develop VOA report",
                        "tracking_prompt": "Track design goals",
                    },
                },
                message_obj=message,
                llm=llm,
                platform="SocraticBots",
                learning_progress_service=service,
                billing_service=None,
                user=None,
                send_callback=self.send,
                get_llm_callback=AsyncMock(return_value=llm),
            )

        frames = [call.args[0] for call in self.send.await_args_list]
        self.assertEqual(
            [frame["type"] for frame in frames],
            ["progress_stream", "progress_stream", "progress_complete"],
        )
        for frame in frames:
            self.assertEqual(frame["conversationId"], "OQ48N")
            self.assertEqual(frame["messageId"], "42241")
        self.assertEqual(frames[-1]["inputTokens"], 2370)
        self.assertEqual(frames[-1]["outputTokens"], 183)
        service._save_progress_assessment.assert_awaited_once()
        self.assertEqual(
            service._save_progress_assessment.call_args.kwargs["content"],
            "## Progress Report\n\n- ✅ Emotion",
        )
        update_message.assert_awaited_once()
        self.assertIs(update_message.call_args.args[1], assessment)

    def test_saved_and_empty_progress_use_subscription_id(self):
        for assessment in ({"content": "Saved assessment"}, None):
            with self.subTest(assessment=assessment):
                coordinator = SimpleNamespace(
                    conversation=self.conversation,
                    learning_progress_service=SimpleNamespace(
                        get_latest_assessment=AsyncMock(return_value=assessment)
                    ),
                    send=self.send,
                )
                async_to_sync(MessageCoordinator.send_latest_learning_progress)(
                    coordinator
                )
                self.assertEqual(
                    self.send.call_args.args[0],
                    {
                        "type": "latest_progress",
                        "conversationId": "OQ48N",
                        "assessment": assessment,
                    },
                )

    def test_failed_saved_progress_lookup_uses_subscription_id(self):
        coordinator = SimpleNamespace(
            conversation=self.conversation,
            learning_progress_service=SimpleNamespace(
                get_latest_assessment=AsyncMock(side_effect=RuntimeError("unavailable"))
            ),
            send=self.send,
        )
        with self.assertLogs(
            "conversations.services.message_coordinator", level="ERROR"
        ):
            async_to_sync(MessageCoordinator.send_latest_learning_progress)(coordinator)
        self.assertEqual(self.send.call_args.args[0]["conversationId"], "OQ48N")
        self.assertIsNone(self.send.call_args.args[0]["assessment"])
