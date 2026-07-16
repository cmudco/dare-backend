from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from billing.models import LiteLLMKey
from conversations.constants import SenderType
from conversations.models import Conversation, Message
from conversations.services.message_helpers.db_helpers import (
    _resolve_litellm_ref,
    _visible_llms_for_user,
    get_ai_message_by_id,
)


class ModelRoutingScopeTests(SimpleTestCase):
    @patch("conversations.services.message_helpers.db_helpers.LLM.objects")
    def test_numeric_model_lookup_starts_from_active_catalog(self, llm_objects):
        active_queryset = llm_objects.filter.return_value
        user = SimpleNamespace(access_code_group=None)

        result = _visible_llms_for_user(user)

        self.assertIs(result, active_queryset)
        llm_objects.filter.assert_called_once_with(is_active=True)

    @patch.object(LiteLLMKey, "visible_for_user")
    def test_authenticated_litellm_lookup_uses_visible_keys(self, visible_for_user):
        user = MagicMock()
        queryset = visible_for_user.return_value
        queryset.filter.return_value.first.return_value = None

        result = _resolve_litellm_ref(
            "00000000-0000-0000-0000-000000000001",
            "model-name",
            user=user,
        )

        self.assertIsNone(result)
        visible_for_user.assert_called_once_with(user)
        queryset.filter.assert_called_once_with(
            pk="00000000-0000-0000-0000-000000000001"
        )


class RegenerationScopeTests(TestCase):
    def test_ai_message_lookup_is_scoped_to_active_conversation(self):
        owned = Conversation.active_objects.create(conversation_id="OWNED")
        other = Conversation.active_objects.create(conversation_id="OTHER")
        other_message = Message.active_objects.create(
            conversation=other,
            sender_type=SenderType.AI_ASSISTANT,
            message="private response",
        )

        result = async_to_sync(get_ai_message_by_id)(
            other_message.id,
            owned.id,
        )

        self.assertIsNone(result)

    def test_ai_message_lookup_returns_message_from_active_conversation(self):
        owned = Conversation.active_objects.create(conversation_id="OWNED")
        owned_message = Message.active_objects.create(
            conversation=owned,
            sender_type=SenderType.AI_ASSISTANT,
            message="response",
        )

        result = async_to_sync(get_ai_message_by_id)(
            owned_message.id,
            owned.id,
        )

        self.assertEqual(result.id, owned_message.id)
