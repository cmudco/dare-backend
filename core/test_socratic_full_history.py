"""Long Socratic interviews retain early answers in both prompt modes."""

from types import SimpleNamespace

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TransactionTestCase

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from conversations.services.message_validation_service import MessageValidationService
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.llm_helpers.socratic_helpers import (
    build_advanced_socratic_messages,
    build_classic_socratic_messages,
)
from users.constants import AuthSourceChoice
from users.models import User


def request_for(
    conversation=None, *, platform=AuthSourceChoice.SOCRATIC_BOTS, **payload
):
    return LLMQueryRequestBuilder.from_message_data(
        message="Continue the interview",
        conversation=conversation,
        user=conversation.user if conversation else None,
        llm=SimpleNamespace(provider="gemini"),
        platform=platform,
        message_data=MessageValidationService.validate_and_parse(
            {"message": "Continue the interview", **payload}
        ),
    )


class HistoryPolicyTests(SimpleTestCase):
    def test_socratic_includes_full_history_despite_generic_or_stale_limits(self):
        for payload in ({}, {"history_limit": 10}, {"history_limit": 20}):
            with self.subTest(payload=payload):
                self.assertEqual(request_for(**payload).context.history_limit, 0)

    def test_standard_chat_retains_requested_and_default_limits(self):
        self.assertEqual(request_for(platform=None).context.history_limit, 10)
        self.assertEqual(
            request_for(platform=None, history_limit=30).context.history_limit, 30
        )


class LongInterviewTests(TransactionTestCase):
    def test_both_modes_include_every_prior_exchange(self):
        user = User.objects.create_user(email="history@example.test", password="test")
        conversation = Conversation.active_objects.create(user=user, title="Reflection")
        prior = []
        for index in range(60):
            text = f"Earlier interview message {index:03d}."
            prior.append(text)
            Message.active_objects.create(
                conversation=conversation,
                sender_type=(
                    SenderType.PLAYER if index % 2 == 0 else SenderType.AI_ASSISTANT
                ),
                message=text,
            )
        # Production saves the current user message and assistant placeholder
        # before constructing history; those two rows must not be replayed.
        Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.PLAYER,
            message="Continue the interview",
        )
        Message.active_objects.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="",
        )
        for advanced, build in (
            (False, build_classic_socratic_messages),
            (True, build_advanced_socratic_messages),
        ):
            with self.subTest(advanced=advanced):
                request = request_for(conversation, is_advanced=advanced)
                result = async_to_sync(build)(request, None)
                prompt = "\n".join(message["content"] for message in result.messages)
                for text in prior:
                    self.assertEqual(prompt.count(text), 1)
                # Advanced mode also includes the question in its system prompt.
                history = next(
                    s for s in result.context_trace["stages"] if s["kind"] == "history"
                )
                self.assertEqual(history["turns"], 60)
                self.assertEqual(history["limit"], 0)
                self.assertIn("Continue the interview", prompt)
