"""Loading a chat's messages costs a fixed number of queries, however many files it has."""

from asgiref.sync import async_to_sync
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from conversations.constants import SenderType
from conversations.models import Conversation, Message, Snippet
from core.services.conversation_service import ConversationService
from files.models import DocumentOcrRequest, File, Tag
from libraries.models import SharedLibrary
from users.models import User


class MessageListQueryCountTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="message-queries@example.test", password="test-only"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.conversation = Conversation.active_objects.create(
            user=self.user, title="Query count"
        )
        self.library = SharedLibrary.active_objects.create(
            name="Pensions", slug="pensions"
        )
        self.turns = 0

    def add_turn(self):
        self.turns += 1
        n = self.turns
        attached = self.make_file(f"attached-{n}.pdf")
        cited = self.make_file(f"cited-{n}.pdf")
        question = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.PLAYER,
            message=f"Question {n}",
        )
        question.files.add(attached)
        answer = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message=f"Answer {n}",
        )
        Snippet.active_objects.create(
            message=answer,
            file=cited,
            text="From a file",
            similarity_score=0.9,
            chunk_index=0,
        )
        Snippet.active_objects.create(
            message=answer,
            library=self.library,
            text="From a library",
            similarity_score=0.8,
            chunk_index=0,
        )

    def make_file(self, name):
        file = File.active_objects.create(
            user=self.user, file=f"files/{name}", name=name
        )
        file.tags.add(Tag.objects.create(user=self.user, label=f"tag-{name}"))
        DocumentOcrRequest.objects.create(file=file)
        return file

    def count_queries(self, load):
        with CaptureQueriesContext(connection) as queries:
            load()
        return len(queries)

    def assert_constant_queries(self, load):
        self.add_turn()
        few = self.count_queries(load)
        for _ in range(3):
            self.add_turn()
        many = self.count_queries(load)
        self.assertEqual(few, many)

    def test_messages_endpoint(self):
        url = f"/api/conversations/{self.conversation.conversation_id}/messages/"

        def load():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

        self.assert_constant_queries(load)

    def test_socket_history(self):
        def load():
            async_to_sync(ConversationService().fetch_chat_history_from_db)(
                self.conversation
            )

        self.assert_constant_queries(load)
