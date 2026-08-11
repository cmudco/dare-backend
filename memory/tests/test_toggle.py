"""The memory toggle — the switch that gates the whole pipeline.

`use_memory` decides two things on every turn: whether stored memories are
injected into the prompt, and whether the completed turn is read by the
writer. Off means nothing is recalled and nothing is written down, which
makes this the system's consent surface — so it has to survive a reload.

It did not, once: the frontend PATCHed `memoryEnabled` but the field existed
on no model and in no serializer, so the update was silently dropped and the
toggle reset to off on the next page load, with the pipeline quietly going
with it.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import Conversation


class MemoryToggleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="toggle-tester@example.com", password="x"
        )
        cls.conversation = Conversation.active_objects.create(
            user=cls.user, conversation_id="toggle-conv", title="toggle"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_toggle_defaults_to_off(self):
        self.assertFalse(self.conversation.memory_enabled)

    def test_turning_it_on_survives_a_reload(self):
        response = self.client.patch(
            f"/api/conversations/{self.conversation.conversation_id}/",
            {"memoryEnabled": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        # Round-trips through the camelCase renderer.
        self.assertTrue(response.json()["memoryEnabled"])

        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.memory_enabled)

        # What the page reads when it reopens the conversation.
        reloaded = self.client.get(
            f"/api/conversations/{self.conversation.conversation_id}/"
        )
        self.assertTrue(reloaded.json()["memoryEnabled"])

    def test_turning_it_back_off_also_persists(self):
        self.conversation.memory_enabled = True
        self.conversation.save(update_fields=["memory_enabled"])

        self.client.patch(
            f"/api/conversations/{self.conversation.conversation_id}/",
            {"memoryEnabled": False},
            format="json",
        )
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.memory_enabled)


class MemoryGateTests(TestCase):
    """The switch reaches the value both halves of the pipeline branch on."""

    def test_the_socket_payload_carries_the_toggle_through(self):
        # The chain the toggle travels: socket payload →
        # MessageValidationService → ContextConfig.use_memory, which is what
        # the read gate (build_standard_messages) and the write gate
        # (MessageCoordinator's writer enqueue) both branch on.
        from conversations.services.message_validation_service import \
            MessageValidationService

        parsed_on = MessageValidationService.validate_and_parse(
            {"message": "hi", "use_memory": True}
        )
        parsed_off = MessageValidationService.validate_and_parse({"message": "hi"})

        self.assertTrue(parsed_on["use_memory"])
        # Absent means off: memory is opt-in, never inferred.
        self.assertFalse(parsed_off["use_memory"])

    def test_an_off_toggle_means_no_memory_context_is_requested(self):
        from core.services.dtos.context_dto import ContextConfig

        self.assertFalse(ContextConfig().use_memory)
        self.assertTrue(ContextConfig(use_memory=True).use_memory)
