from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from google.genai.types import FinishReason

from core.services.dtos.stream_event_dto import StreamEventKind
from core.services.llm_utils.stream_processors import (OUT_OF_TOKENS_NOTE,
                                                       ClaudeStreamProcessor,
                                                       GeminiStreamProcessor,
                                                       OpenAIStreamProcessor)


def _stream(*chunks):
    async def gen():
        for chunk in chunks:
            yield chunk

    return gen()


def _ending(kind, reason=None, error=None):
    return SimpleNamespace(
        type=kind,
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=40, output_tokens=2048),
            incomplete_details=SimpleNamespace(reason=reason) if reason else None,
            error=SimpleNamespace(message=error) if error else None,
        ),
    )


def _run(*chunks):
    async def collect():
        return [
            event
            async for event in OpenAIStreamProcessor.process_responses_api_stream(
                _stream(*chunks)
            )
        ]

    events = async_to_sync(collect)()
    text = "".join(e.text for e in events if e.kind == StreamEventKind.TEXT_DELTA)
    usage = [e.usage for e in events if e.kind == StreamEventKind.USAGE]
    return text, usage


class ResponsesStreamEndingTests(SimpleTestCase):
    def test_out_of_tokens_with_no_answer_tells_user_and_bills(self):
        text, usage = _run(_ending("response.incomplete", reason="max_output_tokens"))

        self.assertTrue(text.startswith("_The response stopped"))
        self.assertIn("Raise Max Tokens", text)
        self.assertEqual(usage[0]["output_tokens"], 2048)

    def test_out_of_tokens_mid_answer_keeps_the_partial_answer(self):
        text, _ = _run(
            SimpleNamespace(type="response.output_text.delta", delta="Partial"),
            _ending("response.incomplete", reason="max_output_tokens"),
        )

        self.assertTrue(text.startswith("Partial\n\n_The response stopped"))

    def test_failed_response_surfaces_the_provider_error(self):
        text, _ = _run(_ending("response.failed", error="server overloaded"))

        self.assertEqual(text, "Error: server overloaded")


def _collect(processor, *chunks):
    async def collect():
        return [event async for event in processor(_stream(*chunks))]

    events = async_to_sync(collect)()
    return "".join(e.text for e in events if e.kind == StreamEventKind.TEXT_DELTA)


def _chat_chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


class OutOfTokensAcrossProvidersTests(SimpleTestCase):
    def test_chat_completions_length_stop(self):
        text = _collect(
            OpenAIStreamProcessor.process_chat_completion_stream,
            _chat_chunk("Partial"),
            _chat_chunk(finish_reason="length"),
        )

        self.assertEqual(text, "Partial\n\n" + OUT_OF_TOKENS_NOTE)

    def test_chat_completions_normal_stop_adds_nothing(self):
        text = _collect(
            OpenAIStreamProcessor.process_chat_completion_stream,
            _chat_chunk("Done"),
            _chat_chunk(finish_reason="stop"),
        )

        self.assertEqual(text, "Done")

    def test_claude_max_tokens_stop(self):
        text = _collect(
            ClaudeStreamProcessor.process_stream,
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="max_tokens"),
                usage=SimpleNamespace(output_tokens=256),
            ),
        )

        self.assertEqual(text, OUT_OF_TOKENS_NOTE)

    def test_gemini_max_tokens_with_no_text_parts(self):
        text = _collect(
            GeminiStreamProcessor.process_stream,
            SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        finish_reason=FinishReason.MAX_TOKENS,
                        content=SimpleNamespace(parts=None),
                    )
                ],
                usage_metadata=None,
            ),
        )

        self.assertEqual(text, OUT_OF_TOKENS_NOTE)
