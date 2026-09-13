from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from conversations.services.message_helpers.learning_progress_helpers import (
    run_learning_progress_stream,
)
from core.services.learning_progress_service import LearningProgressService


class OptionalLearningProgressTests(SimpleTestCase):
    def setUp(self):
        self.service = LearningProgressService()
        self.service._get_default_progress_llm = AsyncMock()
        self.service._get_ai_service = AsyncMock()
        self.service._get_conversation_history = AsyncMock(return_value="User: hello")
        self.service._get_previous_assessment = AsyncMock(return_value="No assessment")
        self.service._save_progress_assessment = AsyncMock()
        self.send = AsyncMock()
        self.get_llm = AsyncMock(return_value=SimpleNamespace(id=1))
        self.billing = SimpleNamespace(
            check_streaming_credit_usage=AsyncMock(return_value=(True, None))
        )

    def run_progress(self, meta, service=None):
        async_to_sync(run_learning_progress_stream)(
            conversation=SimpleNamespace(id=1),
            message_data={"bot_meta": meta},
            message_obj=SimpleNamespace(id=2),
            llm=SimpleNamespace(id=1),
            platform="SocraticBots",
            learning_progress_service=service or self.service,
            billing_service=self.billing,
            user=SimpleNamespace(id=3),
            send_callback=self.send,
            get_llm_callback=self.get_llm,
        )

    def test_missing_or_whitespace_configuration_skips_all_assessment_work(self):
        for missing in (None, "", "  \n"):
            for field in ("learning_goals", "tracking_prompt"):
                with self.subTest(field=field, value=missing):
                    meta = {
                        "learning_goals": "Understand energy",
                        "tracking_prompt": "Assess understanding",
                    }
                    meta[field] = missing
                    self.run_progress(meta)
        self.run_progress(None)
        self.get_llm.assert_not_awaited()
        self.service._get_ai_service.assert_not_awaited()
        self.service._save_progress_assessment.assert_not_awaited()
        self.billing.check_streaming_credit_usage.assert_not_awaited()
        self.send.assert_not_awaited()

    def test_direct_service_skips_incomplete_config_without_logging_exception(self):
        async def collect(goals, prompt):
            return [
                item
                async for item in self.service.assess_learning_progress(
                    None, goals, prompt
                )
            ]

        with patch("core.services.learning_progress_service.logger.exception") as log:
            for goals, prompt in [
                (None, "Track"),
                ("Learn", None),
                (" ", "Track"),
                ("Learn", "\n"),
            ]:
                self.assertEqual(async_to_sync(collect)(goals, prompt), [])
            log.assert_not_called()
        self.service._get_default_progress_llm.assert_not_awaited()
        self.service._get_conversation_history.assert_not_awaited()

    def test_complete_configuration_still_streams(self):
        from core.services.dtos import LLMStreamEvent

        async def chunks(**kwargs):
            yield LLMStreamEvent.text_delta("Making progress")

        self.service._get_ai_service.return_value = SimpleNamespace(
            stream_chat_completion=chunks
        )

        async def collect():
            return [
                item
                async for item in self.service.assess_learning_progress(
                    None, "Learn", "Track", llm=SimpleNamespace(id=1)
                )
            ]

        result = async_to_sync(collect)()
        self.assertTrue(any(text == "Making progress" for text, usage in result))
        self.service._get_ai_service.assert_awaited_once()

    def test_provider_error_is_not_billed_or_saved_as_an_assessment(self):
        async def failed(**kwargs):
            yield "", {"error": True, "message": "Provider unavailable"}

        service = SimpleNamespace(
            assess_learning_progress=failed, _save_progress_assessment=AsyncMock()
        )
        self.run_progress(
            {"learning_goals": "Learn", "tracking_prompt": "Track"}, service
        )
        self.assertEqual(self.send.call_args.args[0]["type"], "progress_error")
        self.billing.check_streaming_credit_usage.assert_not_awaited()
        service._save_progress_assessment.assert_not_awaited()
