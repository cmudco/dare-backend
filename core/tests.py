from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from core.services.background_model_service import BackgroundModelResult
from core.services.conversation_service import ConversationService

# Create your tests here.


class SanitizeTitleTests(SimpleTestCase):
    """Model-generated conversation titles must fit the 255-char column and
    read like titles (small models return markdown headers or paragraphs)."""

    def test_markdown_and_quotes_stripped(self):
        from core.services.conversation_service import ConversationService

        self.assertEqual(
            ConversationService._sanitize_title('# "Claude AI Overview"'),
            "Claude AI Overview",
        )

    def test_multiline_reply_takes_first_line(self):
        from core.services.conversation_service import ConversationService

        raw = "Funding Memo Discussion\n\nThis conversation covers..."
        self.assertEqual(
            ConversationService._sanitize_title(raw), "Funding Memo Discussion"
        )

    def test_long_reply_truncated_under_column_limit(self):
        from core.services.conversation_service import ConversationService

        title = ConversationService._sanitize_title("word " * 100)
        self.assertLessEqual(len(title), 120)
        self.assertTrue(title.endswith("…"))

    def test_empty_falls_back(self):
        from core.services.conversation_service import ConversationService

        self.assertEqual(ConversationService._sanitize_title(""), "New Chat")

    def test_provider_error_text_never_becomes_title(self):
        from core.services.conversation_service import ConversationService

        raw = "Error: OpenAI error: Incorrect API key provided: sk-proj-***"
        self.assertEqual(ConversationService._sanitize_title(raw), "New Chat")
        self.assertEqual(ConversationService._sanitize_title("###"), "New Chat")


class TitleGenerationModelTests(SimpleTestCase):
    def test_uses_the_background_model_service(self):
        complete = AsyncMock(
            return_value=BackgroundModelResult(
                value="Useful Conversation Title",
                route=None,
                input_tokens=10,
                output_tokens=3,
            )
        )
        with patch(
            "core.services.background_model_service.BackgroundModelService.complete_text",
            new=complete,
        ):
            title = async_to_sync(ConversationService().generate_title)(
                "Help me analyze this document",
                user=object(),
            )

        self.assertEqual(title, "Useful Conversation Title")
        self.assertEqual(
            complete.await_args.kwargs["description"],
            "Conversation title generation",
        )

    def test_failure_returns_new_chat(self):
        with patch(
            "core.services.background_model_service.BackgroundModelService.complete_text",
            new=AsyncMock(side_effect=RuntimeError("Luna unavailable")),
        ):
            title = async_to_sync(ConversationService().generate_title)(
                "Help me analyze this document",
                user=object(),
            )

        self.assertEqual(title, "New Chat")
