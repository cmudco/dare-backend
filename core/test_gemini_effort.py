from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from core.services.gemini_service import GeminiService


class GeminiEffortTests(SimpleTestCase):
    def service(self, identifier="gemini-3.8-flash", supports_effort=True):
        return GeminiService(
            SimpleNamespace(
                identifier=identifier,
                is_reasoning=True,
                supports_effort=supports_effort,
                default_effort="medium",
                supports_temperature=True,
            ),
            api_key="test-key",
        )

    def test_supported_levels_and_default_reach_google_config(self):
        service = self.service()
        for requested, expected in [
            (None, "MEDIUM"),
            ("low", "LOW"),
            ("medium", "MEDIUM"),
            ("high", "HIGH"),
            ("xhigh", "HIGH"),
            ("max", "HIGH"),
        ]:
            with self.subTest(effort=requested):
                config = service._build_generation_config(
                    100, 0.7, None, effort=requested
                )
                self.assertEqual(config.thinking_config.thinking_level, expected)

    def test_older_models_and_disabled_effort_keep_provider_defaults(self):
        for service in [
            self.service("gemini-2.5-flash"),
            self.service(supports_effort=False),
        ]:
            self.assertIsNone(
                service._build_generation_config(
                    100, 0.7, None, effort="low"
                ).thinking_config
            )

    async def test_public_stream_passes_effort_to_provider(self):
        service = self.service()

        async def chunks():
            if False:
                yield None

        generate = AsyncMock(return_value=chunks())
        service._client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content_stream=generate)
            )
        )
        async for _ in service.stream_chat_completion(
            [{"role": "user", "content": "Hello"}], effort="low"
        ):
            pass
        self.assertEqual(
            generate.await_args.kwargs["config"].thinking_config.thinking_level, "LOW"
        )
