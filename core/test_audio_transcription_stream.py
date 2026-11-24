from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from core.services.llm_helpers import media_helpers


def file(pk, name):
    return SimpleNamespace(id=pk, name=name, size=10, media_type="audio")


def parts(*texts):
    async def stream(file_obj, model, language):
        for index, text in enumerate(texts):
            yield {"text": text, "chunk_index": index}

    return stream


class AudioTranscriptionStreamTests(SimpleTestCase):
    async def run_transcription(self, files, stream):
        with patch.object(
            media_helpers, "get_audio_or_video_files", AsyncMock(return_value=files)
        ), patch.object(
            media_helpers.AudioTranscriptionService,
            "transcribe_audio_file_streaming",
            side_effect=stream,
        ):
            return [
                item
                async for item in media_helpers.execute_audio_transcription(
                    [1], "whisper-1"
                )
            ]

    async def test_reply_holds_each_part_once(self):
        chunks = await self.run_transcription(
            [file(1, "talk.mp3")], parts("Hello team.", "Thank you.")
        )

        reply = "".join(text for text, _ in chunks)
        self.assertEqual(
            reply, "**Transcription of `talk.mp3`**\n\nHello team. Thank you."
        )
        final_text, usage = chunks[-1]
        self.assertEqual(final_text, "")
        self.assertEqual(
            usage["transcription_result"]["text"], "Hello team. Thank you."
        )

    async def test_each_file_starts_its_own_block(self):
        streams = iter([parts("First."), parts("Second.")])
        chunks = await self.run_transcription(
            [file(1, "a.mp3"), file(2, "b.mp3")],
            lambda **kwargs: next(streams)(**kwargs),
        )

        self.assertEqual(
            "".join(text for text, _ in chunks),
            "**Transcription of `a.mp3`**\n\nFirst.\n\n"
            "**Transcription of `b.mp3`**\n\nSecond.",
        )
