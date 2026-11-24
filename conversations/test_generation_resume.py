import asyncio
import json
from unittest.mock import AsyncMock, patch

from asgiref.sync import sync_to_async
from django.test import TransactionTestCase, override_settings

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from conversations.services import generation_registry
from conversations.services.message_coordinator import (
    INTERRUPTED_REPLY,
    MessageCoordinator,
)
from users.models import User


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
@patch.object(generation_registry, "HEARTBEAT_SECONDS", 0.01)
class GenerationResumeTests(TransactionTestCase):
    """A reply outlives the socket that started it: a new socket sees and stops it."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="resume@example.test", password="test-only"
        )
        self.conversation = Conversation.active_objects.create(
            user=self.user, title="Resume"
        )
        self.other_conversation = Conversation.active_objects.create(
            user=self.user, title="Other"
        )
        self.reply = self._reply("")

    def _reply(self, text):
        return Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message=text,
        )

    def _coordinator(self):
        return MessageCoordinator(
            conversation=self.conversation, user=self.user, send_callback=AsyncMock()
        )

    @staticmethod
    def _sent(coordinator):
        return [
            json.loads(c.args[0]) for c in coordinator.send_callback.await_args_list
        ]

    async def _wait_until_live(self, message_id):
        for _ in range(200):
            if await generation_registry.live_ids(self.conversation.id, [message_id]):
                return
            await asyncio.sleep(0.01)
        self.fail("turn never became live")

    async def test_stop_from_a_new_socket_cancels_the_running_turn(self):
        owner = self._coordinator()

        async def endless_stream(message_obj, **_):
            owner._cancellable_message_ids.add(message_obj.id)
            await asyncio.Event().wait()

        owner.stream_ai_response = endless_stream
        turn = asyncio.create_task(owner._run_generation(message_obj=self.reply))
        await self._wait_until_live(self.reply.id)

        reconnected = self._coordinator()
        await reconnected.sync_generations()
        self.assertIn(
            {"type": "generation_active", "messageId": self.reply.id},
            self._sent(reconnected),
        )
        self.assertTrue(await reconnected.request_stop(None))

        await asyncio.wait_for(turn, timeout=2)
        self.assertEqual(
            await generation_registry.live_ids(self.conversation.id, [self.reply.id]),
            set(),
        )

    async def test_stop_is_refused_for_a_turn_in_another_conversation(self):
        await generation_registry.mark_live(self.reply.id, self.conversation.id)

        stopped = await generation_registry.request_stop(
            self.other_conversation.id, self.reply.id
        )

        self.assertFalse(stopped)
        self.assertFalse(await generation_registry.stop_requested(self.reply.id))

    async def test_an_empty_reply_whose_turn_died_is_saved_as_interrupted(self):
        dead = await sync_to_async(self._reply)("")
        answered = await sync_to_async(self._reply)("Done.")
        await generation_registry.mark_live(self.reply.id, self.conversation.id)
        coordinator = self._coordinator()

        await coordinator.sync_generations()
        await coordinator.sync_generations()

        rows = await sync_to_async(dict)(
            Message._base_manager.filter(
                pk__in=[dead.pk, answered.pk, self.reply.pk]
            ).values_list("pk", "message")
        )
        self.assertEqual(rows[dead.pk], INTERRUPTED_REPLY)
        self.assertEqual(rows[answered.pk], "Done.")
        self.assertEqual(rows[self.reply.pk], "")
        pushed = [m for m in self._sent(coordinator) if m.get("id") == dead.pk]
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0]["message"], INTERRUPTED_REPLY)
