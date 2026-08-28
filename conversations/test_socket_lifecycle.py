from types import SimpleNamespace
from unittest.mock import AsyncMock

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


class WorkflowNamespaceDisconnectTests(SimpleTestCase):
    async def test_disconnect_accepts_socketio_reason(self):
        namespace = WorkflowNamespace()
        namespace.sessions["socket-id"] = {
            "user": SimpleNamespace(id=7),
            "subscriptions": set(),
        }

        await namespace.on_disconnect("socket-id", "transport close")

        self.assertEqual(namespace.sessions, {})
