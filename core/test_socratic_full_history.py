"""Long Socratic interviews retain early answers in both prompt modes."""

from types import SimpleNamespace
from unittest import mock

from asgiref.sync import async_to_sync
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

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


DARE_URL = "https://dare.example.test"
SOCRATIC_URL = "https://socratic.example.test"


@mock.patch.multiple(
    "users.utils",
    DARE_FRONTEND_URL=DARE_URL,
    SOCRATIC_BOTS_FRONTEND_URL=SOCRATIC_URL,
)
class StoredHistoryPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="stored@example.test", password="t")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_conversation(self, origin):
        response = self.client.post(
            "/api/conversations/", {"title": "Chat"}, format="json", HTTP_ORIGIN=origin
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Conversation.active_objects.get(
            conversation_id=response.data["conversation_id"]
        )

    def test_socratic_conversations_record_full_history(self):
        conversation = self.create_conversation(SOCRATIC_URL)
        self.assertEqual(conversation.source, AuthSourceChoice.SOCRATIC_BOTS)
        self.assertEqual(conversation.history_limit, 0)

    def test_dare_conversations_keep_the_default_limit(self):
        conversation = self.create_conversation(DARE_URL)
        self.assertEqual(conversation.history_limit, 20)

    def test_export_labels_full_history(self):
        conversation = Conversation.active_objects.create(
            user=self.user, title="Export", history_limit=0
        )
        html = render_to_string(
            "conversations/conversation_export.html",
            {"conversation": conversation, "messages": [], "user": self.user},
        )
        self.assertRegex(html, r"History Limit</div><div[^>]*>All messages<")
