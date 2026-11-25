from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from conversations.constants import SenderType
from conversations.models import Conversation, ConversationSummary, Message
from core.services.dtos import ContextConfig, LLMQueryRequest
from core.services.llm_helpers.db_helpers import (
    get_referenced_conversations_context,
    get_referenced_summaries_context,
)
from core.services.llm_helpers.history_helpers import files_read_turn
from core.services.llm_helpers.standard_message_helpers import (
    append_document_access_status,
    append_saved_system_prompt,
    build_standard_messages,
)


class SavedSystemPromptTests(SimpleTestCase):
    def test_saved_prompt_is_the_system_message_verbatim(self):
        messages = []

        count = append_saved_system_prompt(messages, "  Be rigorous and concise.  ")

        self.assertEqual(
            messages,
            [{"role": "system", "content": "Be rigorous and concise."}],
        )
        self.assertEqual(count, len("Be rigorous and concise."))

    def test_no_saved_prompt_does_not_manufacture_a_system_message(self):
        for prompt in (None, "", "   "):
            messages = []
            self.assertEqual(append_saved_system_prompt(messages, prompt), 0)
            self.assertEqual(messages, [])


class DocumentAccessStatusTests(SimpleTestCase):
    def test_lists_all_selected_files_and_explains_snippet_subset(self):
        messages = []

        append_document_access_status(
            messages,
            sandbox_file_names=[],
            embedding_file_names=["alpha.pdf", "beta.pdf", "gamma.pdf"],
            has_grouped_sources=False,
            has_library_sources=False,
        )

        content = messages[0]["content"]
        self.assertIn("alpha.pdf", content)
        self.assertIn("beta.pdf", content)
        self.assertIn("gamma.pdf", content)
        self.assertIn("query-matched subset", content)

    def test_no_sources_adds_no_hidden_message(self):
        messages = []

        append_document_access_status(
            messages,
            sandbox_file_names=[],
            embedding_file_names=[],
            has_grouped_sources=False,
            has_library_sources=False,
        )

        self.assertEqual(messages, [])


class FreshChatMessageTests(SimpleTestCase):
    @patch(
        "core.services.llm_helpers.standard_message_helpers.get_selected_file_names",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "core.services.llm_helpers.standard_message_helpers.add_semantic_context_to_messages",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "core.services.llm_helpers.standard_message_helpers.get_prompt",
        new_callable=AsyncMock,
        return_value=None,
    )
    async def test_plain_memory_off_chat_sends_only_the_user_message(
        self,
        _get_prompt,
        _add_semantic_context,
        _get_selected_file_names,
    ):
        request = LLMQueryRequest(
            message="Hello",
            user=SimpleNamespace(id=1),
            context=ContextConfig(use_memory=False),
        )

        result = await build_standard_messages(request, None, None)

        self.assertEqual(result.messages, [{"role": "user", "content": "Hello"}])


HELENS = {
    "id": 1,
    "name": "mt-st-helens.docx",
    "content": "File: mt-st-helens.docx\n\nThe 1980 eruption killed 57 people.",
}


@patch(
    "core.services.llm_helpers.standard_message_helpers.get_full_file_contents",
    new_callable=AsyncMock,
    return_value=[HELENS],
)
@patch(
    "core.services.llm_helpers.standard_message_helpers.get_selected_file_names",
    new_callable=AsyncMock,
    return_value=[],
)
@patch(
    "core.services.llm_helpers.standard_message_helpers.get_prompt",
    new_callable=AsyncMock,
    return_value=None,
)
class FileTurnHistoryTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="file-turns@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="file-turns", title="Volcanoes"
        )

    def ask(self, text, file_ids):
        """Build one turn's prompt, then store the reply as the chat flow does."""
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message=text,
        )
        reply = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        request = LLMQueryRequest(
            message=text,
            user=self.user,
            conversation=self.conversation,
            message_obj=reply,
            context=ContextConfig(file_ids=file_ids),
        )
        result = async_to_sync(build_standard_messages)(request, None, None)
        reply.message = f"Answer to {text}"
        reply.context_trace = result.context_trace
        reply.save()
        return result.messages

    def test_each_turn_prompt_is_a_prefix_of_the_next(self, *_):
        first = self.ask("Summarise the eruption", [HELENS["id"]])
        second = self.ask("How many died?", [HELENS["id"]])

        self.assertEqual(second[: len(first)], first)

    def test_unticked_file_stays_named_on_the_turn_that_read_it(self, *_):
        self.ask("Summarise the eruption", [HELENS["id"]])
        second = self.ask("Were your earlier answers right?", [])

        self.assertEqual(
            second[:2],
            [
                files_read_turn(["mt-st-helens.docx"]),
                {"role": "user", "content": "Summarise the eruption"},
            ],
        )
        self.assertNotIn("57 people", str(second))


class ReferencedSummaryScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="summary-owner@example.com",
            password="x",
        )
        other_user = get_user_model().objects.create_user(
            email="summary-other@example.com",
            password="x",
        )
        owned_conversation = Conversation.active_objects.create(
            user=cls.user,
            conversation_id="summary-owned",
            title="Owned conversation",
        )
        other_conversation = Conversation.active_objects.create(
            user=other_user,
            conversation_id="summary-other",
            title="Other conversation",
        )
        cls.owned_summary = ConversationSummary.active_objects.create(
            conversation=owned_conversation,
            summary="Owned summary text.",
        )
        cls.other_summary = ConversationSummary.active_objects.create(
            conversation=other_conversation,
            summary="Other user's private summary.",
        )

    def test_only_includes_summaries_owned_by_the_current_user(self):
        context = get_referenced_summaries_context.func(
            [self.owned_summary.id, self.other_summary.id],
            self.user.id,
        )

        self.assertIn("Owned summary text.", context)
        self.assertNotIn("Other user's private summary.", context)


class ReferencedConversationContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="conversation-owner@example.com",
            password="x",
        )
        cls.conversation = Conversation.active_objects.create(
            user=cls.user,
            conversation_id="referenced-conversation",
            title="Referenced conversation",
        )
        Message.active_objects.create(
            conversation=cls.conversation,
            sender_type=SenderType.PLAYER,
            message="What is the answer?",
        )
        Message.active_objects.create(
            conversation=cls.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="The answer is 42.",
        )

    def test_formats_referenced_conversation_messages(self):
        context = get_referenced_conversations_context.func(
            [self.conversation.conversation_id],
            self.user.id,
        )

        self.assertIn("Referenced Conversation: Referenced conversation", context)
        self.assertIn("User: What is the answer?", context)
        self.assertIn("Assistant: The answer is 42.", context)
