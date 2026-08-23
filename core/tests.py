from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TransactionTestCase

from conversations.models import LLM
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


class TitleGenerationModelTests(TransactionTestCase):
    def setUp(self):
        self.gemini, _ = LLM.objects.get_or_create(
            identifier="gemini-3.1-flash-lite",
            defaults={
                "name": "Gemini 3.1 Flash-Lite",
                "provider": "gemini",
                "is_active": True,
            },
        )
        self.gemini.is_active = True
        self.gemini.save(update_fields=["is_active"])
        self.conversation_model = LLM.objects.create(
            name="Premium conversation model",
            identifier="premium-conversation-model",
            provider="openai",
            is_active=True,
        )

    def test_prefers_gemini_flash_lite_over_conversation_model(self):
        ai_service = AsyncMock()
        ai_service.get_chat_completion.return_value = "Useful Conversation Title"

        with patch(
            "core.services.llm_service.LLMService._get_ai_service",
            new=AsyncMock(return_value=ai_service),
        ) as get_ai_service:
            title = async_to_sync(ConversationService().generate_title)(
                "Help me analyze this document",
                llm=self.conversation_model,
            )

        self.assertEqual(title, "Useful Conversation Title")
        self.assertEqual(get_ai_service.await_args.args[0].pk, self.gemini.pk)

    def test_falls_back_to_conversation_model_if_gemini_call_fails(self):
        failed_service = AsyncMock()
        failed_service.get_chat_completion.side_effect = RuntimeError(
            "Gemini unavailable"
        )
        fallback_service = AsyncMock()
        fallback_service.get_chat_completion.return_value = "Fallback Title"

        with patch(
            "core.services.llm_service.LLMService._get_ai_service",
            new=AsyncMock(side_effect=[failed_service, fallback_service]),
        ) as get_ai_service:
            title = async_to_sync(ConversationService().generate_title)(
                "Help me analyze this document",
                llm=self.conversation_model,
            )

        self.assertEqual(title, "Fallback Title")
        self.assertEqual(get_ai_service.await_count, 2)
        self.assertEqual(
            get_ai_service.await_args_list[1].args[0].pk,
            self.conversation_model.pk,
        )
