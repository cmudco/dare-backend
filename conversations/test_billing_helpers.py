from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from conversations.services.message_helpers.billing_helpers import \
    handle_insufficient_balance

FORMAT_MESSAGE = (
    "conversations.services.message_helpers.billing_helpers"
    ".WebSocketResponseService.format_message"
)


class InsufficientBalanceTests(SimpleTestCase):
    """The mid-stream cutoff must always reach the client.

    Regression: the old path re-billed via finalize_ai_message, which raised
    insufficient_balance against the just-emptied wallet; the bare except
    swallowed it and neither the partial message nor the error event was
    ever sent — a permanent spinner over an empty placeholder.
    """

    def setUp(self):
        self.sends = []
        self.errors = []
        self.message = SimpleNamespace(id=42)

        async def send(payload):
            self.sends.append(payload)

        async def send_error(code, message, details):
            self.errors.append(code)

        self.send = send
        self.send_error = send_error

    async def test_partial_message_and_error_are_sent(self):
        billing = SimpleNamespace(
            finalize_ai_message_no_billing=lambda msg, text, usage: (msg, 0)
        )
        with patch(FORMAT_MESSAGE, new=AsyncMock(return_value={"type": "message"})):
            await handle_insufficient_balance(
                self.message,
                "partial answer",
                {"input_tokens": 10, "output_tokens": 5},
                {"error": "insufficient_balance", "message": "out of credits"},
                billing,
                self.send,
                self.send_error,
            )

        self.assertEqual(self.sends, [{"type": "message"}])
        self.assertEqual(self.errors, ["insufficient_balance"])

    async def test_finalize_failure_does_not_block_delivery(self):
        def explode(msg, text, usage):
            raise ValueError("wallet already empty")

        billing = SimpleNamespace(finalize_ai_message_no_billing=explode)
        with patch(FORMAT_MESSAGE, new=AsyncMock(return_value={"type": "message"})):
            await handle_insufficient_balance(
                self.message,
                "partial answer",
                {},
                {"error": "insufficient_balance"},
                billing,
                self.send,
                self.send_error,
            )

        self.assertEqual(self.sends, [{"type": "message"}])
        self.assertEqual(self.errors, ["insufficient_balance"])

    async def test_empty_partial_text_gets_visible_explanation(self):
        captured = {}

        def record(msg, text, usage):
            captured["text"] = text
            return msg, 0

        billing = SimpleNamespace(finalize_ai_message_no_billing=record)
        with patch(FORMAT_MESSAGE, new=AsyncMock(return_value={"type": "message"})):
            await handle_insufficient_balance(
                self.message,
                "",
                {},
                {"error": "insufficient_balance"},
                billing,
                self.send,
                self.send_error,
            )

        self.assertIn("credits ran out", captured["text"])
