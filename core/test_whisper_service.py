from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from config import env
from core.services.whisper_service import WhisperService


class WhisperServiceTests(SimpleTestCase):
    @patch.object(env, "TRANSCRIPTION_MODEL", "city-whisper")
    @patch.object(env, "TRANSCRIPTION_API_KEY", "gateway-key")
    @patch.object(env, "TRANSCRIPTION_BASE_URL", "https://gateway.example.org/v1")
    async def test_gateway_settings_alone_are_enough(self):
        with patch(
            "core.services.whisper_service.get_provider_api_key",
            AsyncMock(side_effect=AssertionError("no OpenAI key needed")),
        ):
            service = await WhisperService.for_platform()

        self.assertEqual(
            str(service.client.base_url), "https://gateway.example.org/v1/"
        )
        self.assertEqual(service.client.api_key, "gateway-key")

        service.client.audio.transcriptions.create = AsyncMock(return_value="hello")
        self.assertEqual(await service.transcribe(b"audio", language="en"), "hello")
        service.client.audio.transcriptions.create.assert_awaited_once_with(
            model="city-whisper", file=b"audio", response_format="text", language="en"
        )

    @patch.object(env, "TRANSCRIPTION_API_KEY", "")
    @patch.object(env, "TRANSCRIPTION_BASE_URL", None)
    async def test_unset_settings_use_openai_with_the_openai_key(self):
        with patch(
            "core.services.whisper_service.get_provider_api_key",
            AsyncMock(return_value="sk-openai"),
        ):
            service = await WhisperService.for_platform()

        self.assertEqual(str(service.client.base_url), "https://api.openai.com/v1/")
        self.assertEqual(service.client.api_key, "sk-openai")
