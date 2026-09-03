from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from conversations.services.summary_service import generate_conversation_summary
from core.services.background_model_service import BackgroundModelResult


class ConversationSummaryModelTests(SimpleTestCase):
    @patch(
        "conversations.services.summary_service.BackgroundModelService.complete_text",
        new_callable=AsyncMock,
    )
    @patch(
        "conversations.services.summary_service._build_transcript",
        return_value="User: question\nAssistant: answer",
    )
    def test_uses_the_shared_background_model_service(self, _transcript, complete):
        llm = object()
        complete.return_value = BackgroundModelResult(
            value="A concise summary.",
            route=SimpleNamespace(persisted_llm=llm),
            input_tokens=24,
            output_tokens=6,
        )
        user = object()
        conversation = SimpleNamespace(
            id=1,
            conversation_id="summary-test",
            user=user,
            user_id=1,
            bot_id=None,
        )

        result = generate_conversation_summary(conversation, 3)

        self.assertEqual(result.summary, "A concise summary.")
        self.assertEqual(result.llm, llm)
        self.assertEqual(result.input_tokens, 24)
        self.assertEqual(result.output_tokens, 6)
        self.assertEqual(complete.await_args.kwargs["user"], user)
        self.assertEqual(
            complete.await_args.kwargs["description"],
            "Conversation summary for summary-test",
        )
