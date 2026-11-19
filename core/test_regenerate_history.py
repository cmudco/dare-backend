from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from conversations.constants import MAX_TOOL_ROUNDS, SenderType, ToolCallOrigin
from conversations.models import Conversation, Message, MessageToolCall
from conversations.services.message_helpers.db_helpers import (
    fetch_preceding_user_message,
)
from core.services.llm_helpers.history_helpers import get_conversation_history
from core.services.llm_helpers.tool_turn_helpers import TOOL_BUDGET_SPENT_NOTE


class RegenerateHistoryTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email="regen@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=user, conversation_id="regen-history", title="Charts"
        )
        self.intro = self.add(SenderType.PLAYER, "Hi")
        self.add(SenderType.AI_ASSISTANT, "Hello")
        self.first_ask = self.add(SenderType.PLAYER, "Chart the first 5 years")
        self.first_reply = self.add(SenderType.AI_ASSISTANT, "Chart of 1871-1875")
        self.add(SenderType.PLAYER, "Actually the last 5 years")
        self.latest_reply = self.add(SenderType.AI_ASSISTANT, "")

    def add(self, sender, text):
        return Message.active_objects.create(
            conversation=self.conversation, sender_type=sender, message=text
        )

    def contents(self, before):
        history = async_to_sync(get_conversation_history)(
            self.conversation, before=before
        )
        return [turn["content"] for turn in history]

    def test_regenerating_an_older_reply_sees_only_what_came_before_it(self):
        prompt = async_to_sync(fetch_preceding_user_message)(
            self.conversation, self.first_reply
        )

        self.assertEqual(prompt, self.first_ask)
        self.assertEqual(self.contents(self.first_reply), ["Hi", "Hello"])

    def test_a_new_reply_sees_every_earlier_turn(self):
        self.assertEqual(
            self.contents(self.latest_reply),
            ["Hi", "Hello", "Chart the first 5 years", "Chart of 1871-1875"],
        )


class ToolBudgetHistoryTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            email="budget@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=user, conversation_id="budget-history", title="Edits"
        )

    def reply_after_tool_rounds(self, rounds):
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="Make eight edits",
        )
        reply = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="Four of the eight edits are done.",
        )
        for round_index in range(1, rounds + 1):
            MessageToolCall.objects.create(
                message=reply,
                tool_call_id=f"call-{round_index}",
                tool_name="update_artifact",
                server_slug="dare",
                origin=ToolCallOrigin.DARE,
                status="completed",
                round_index=round_index,
                arguments={},
                result="ok",
            )
        Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message="Carry on",
        )
        next_reply = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        history = async_to_sync(get_conversation_history)(
            self.conversation, before=next_reply
        )
        return [turn["content"] for turn in history]

    def test_a_reply_cut_off_at_the_tool_limit_keeps_the_note_it_was_given(self):
        contents = self.reply_after_tool_rounds(MAX_TOOL_ROUNDS)

        self.assertEqual(
            contents[-2:],
            [TOOL_BUDGET_SPENT_NOTE, "Four of the eight edits are done."],
        )

    def test_a_reply_that_stopped_on_its_own_gets_no_note(self):
        contents = self.reply_after_tool_rounds(MAX_TOOL_ROUNDS - 1)

        self.assertNotIn(TOOL_BUDGET_SPENT_NOTE, contents)
