import anthropic
import httpx
import openai
from django.test import SimpleTestCase
from google.genai import errors as genai_errors

from core.services.llm_utils.error_handlers import (
    CONTEXT_OVERFLOW_MESSAGE,
    ClaudeErrorHandler,
    GeminiErrorHandler,
    OpenAIErrorHandler,
)


def _response(body: dict) -> httpx.Response:
    return httpx.Response(400, json=body, request=httpx.Request("POST", "https://x"))


class ContextOverflowTests(SimpleTestCase):
    def test_claude_prompt_too_long(self):
        body = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "prompt is too long: 251639 tokens > 200000 maximum",
            },
        }
        error = anthropic.BadRequestError(
            "Error code: 400", response=_response(body), body=body
        )

        self.assertEqual(
            ClaudeErrorHandler.format_error(error), CONTEXT_OVERFLOW_MESSAGE
        )

    def test_openai_context_length_exceeded(self):
        body = {
            "error": {
                "message": "Your input exceeds the context window of this model.",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        }
        error = openai.BadRequestError(
            "Error code: 400", response=_response(body), body=body["error"]
        )

        self.assertEqual(
            OpenAIErrorHandler.format_error(error), CONTEXT_OVERFLOW_MESSAGE
        )

    def test_gemini_input_token_count(self):
        error = genai_errors.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "The input token count (1300000) exceeds the maximum "
                    "number of tokens allowed (1048576).",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )

        self.assertEqual(
            GeminiErrorHandler.format_error(error), CONTEXT_OVERFLOW_MESSAGE
        )

    def test_other_bad_requests_keep_the_provider_message(self):
        body = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "bad image"},
        }
        error = anthropic.BadRequestError(
            "Error code: 400", response=_response(body), body=body
        )

        self.assertEqual(
            ClaudeErrorHandler.format_error(error),
            "Claude error (invalid_request_error): bad image",
        )
