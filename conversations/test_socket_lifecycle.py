from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from conversations.namespaces.chat import ChatNamespace
from conversations.namespaces.workflow import WorkflowNamespace


class ChatNamespaceDisconnectTests(SimpleTestCase):
    async def test_disconnect_uses_subscription_snapshot(self):
        namespace = ChatNamespace()
        subscriptions = {"conversation-a", "conversation-b"}
        namespace.sessions["socket-id"] = {
            "user": SimpleNamespace(id=7),
            "subscriptions": subscriptions,
            "is_public": False,
        }
        namespace.coordinators = {
            "socket-id_conversation-a": object(),
            "socket-id_conversation-b": object(),
        }

        async def mutate_live_set(_conversation_id):
            subscriptions.add("late-conversation")

        namespace._pause_conversation_artifacts = AsyncMock(side_effect=mutate_live_set)

        await namespace.on_disconnect("socket-id", "transport close")

        paused_conversations = {
            call.args[0]
            for call in namespace._pause_conversation_artifacts.await_args_list
        }
        self.assertEqual(
            paused_conversations,
            {"conversation-a", "conversation-b"},
        )
        self.assertEqual(namespace.sessions, {})
        self.assertEqual(namespace.coordinators, {})

    async def test_disconnect_accepts_legacy_call_without_reason(self):
        namespace = ChatNamespace()
        namespace.sessions["socket-id"] = {
            "user": None,
            "subscriptions": set(),
            "is_public": True,
        }

        await namespace.on_disconnect("socket-id")

        self.assertEqual(namespace.sessions, {})


class ChatNamespaceSwitchAwayTests(SimpleTestCase):
    """A turn keeps streaming while the user views another chat and comes back."""

    def setUp(self):
        self.namespace = ChatNamespace()
        self.namespace.sessions["socket-id"] = {
            "user": SimpleNamespace(id=7),
            "subscriptions": {"conv"},
            "is_public": False,
        }
        self.namespace.conversation_service = SimpleNamespace(
            get_conversation=AsyncMock(return_value=SimpleNamespace(id=1))
        )
        self.namespace._pause_conversation_artifacts = AsyncMock()
        self.running = SimpleNamespace(
            active_generation_ids=lambda: [42],
            cancel_generation=lambda message_id: message_id == 42,
            send_conversation_history=AsyncMock(),
            send_active_generations=AsyncMock(),
            send_latest_learning_progress=AsyncMock(),
        )
        self.namespace.coordinators = {"socket-id_conv": self.running}
        patcher = patch("conversations.namespaces.chat.sio")
        patcher.start().configure_mock(enter_room=AsyncMock(), leave_room=AsyncMock())
        self.addCleanup(patcher.stop)

    async def test_switch_away_and_back_keeps_the_running_turn_stoppable(self):
        await self.namespace.on_unsubscribe_conversation(
            "socket-id", {"conversationId": "conv"}
        )
        self.assertIs(self.namespace.coordinators["socket-id_conv"], self.running)

        result = await self.namespace.on_subscribe_conversation(
            "socket-id", {"conversationId": "conv"}
        )

        self.assertTrue(result["success"])
        self.assertIs(self.namespace.coordinators["socket-id_conv"], self.running)
        self.running.send_active_generations.assert_awaited_once()
        stop = await self.namespace.on_stop_generation(
            "socket-id", {"conversationId": "conv", "messageId": 42}
        )
        self.assertEqual(stop, {"success": True})

    async def test_unsubscribe_drops_an_idle_coordinator(self):
        self.running.active_generation_ids = lambda: []

        await self.namespace.on_unsubscribe_conversation(
            "socket-id", {"conversationId": "conv"}
        )

        self.assertEqual(self.namespace.coordinators, {})


class WorkflowNamespaceDisconnectTests(SimpleTestCase):
    async def test_disconnect_accepts_socketio_reason(self):
        namespace = WorkflowNamespace()
        namespace.sessions["socket-id"] = {
            "user": SimpleNamespace(id=7),
            "subscriptions": set(),
        }

        await namespace.on_disconnect("socket-id", "transport close")

        self.assertEqual(namespace.sessions, {})
