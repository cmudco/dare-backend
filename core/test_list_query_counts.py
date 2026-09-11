"""List endpoints must not issue one query per row (Sentry N+1 findings)."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from conversations.constants import SenderType
from conversations.models import Artifact, Conversation, Message
from dare_tools.models import DareTool
from files.models import File, Folder, Tag
from mcp.models import MCPServer
from prompts.models import Prompt, PublishedPrompt


class ListQueryCountTests(APITestCase):
    """Each endpoint's query count must be the same for 2 rows and for 6."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="queries@example.com", password="pw"
        )
        self.client.force_authenticate(self.user)
        self.server = MCPServer.active_objects.create(name="Server", slug="server")
        self.tool = DareTool.active_objects.create(
            name="Diagram", slug="diagram", function_name="create_diagram"
        )

    def queries_for(self, url, build, sizes=(2, 6)):
        counts = []
        built = 0
        for size in sizes:
            for _ in range(size - built):
                build()
            built = size
            with CaptureQueriesContext(connection) as context:
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.content[:200])
            counts.append(len(context.captured_queries))
        return counts

    def assert_flat(self, url, build):
        small, large = self.queries_for(url, build)
        self.assertEqual(
            small, large, f"{url}: {small} queries for 2 rows, {large} for 6"
        )

    def make_prompt(self):
        prompt = Prompt.active_objects.create(user=self.user, title="t", content="c")
        PublishedPrompt.active_objects.create(prompt=prompt, description="d")
        return prompt

    def make_file(self):
        file = File.active_objects.create(
            user=self.user,
            name="doc.pdf",
            file=SimpleUploadedFile("doc.pdf", b"%PDF-test"),
            file_type="application/pdf",
        )
        tag = Tag.objects.create(user=self.user, label=f"tag-{file.pk}")
        file.tags.add(tag)
        return file

    def test_prompts_list(self):
        self.assert_flat("/api/prompts/", self.make_prompt)

    def test_files_list(self):
        self.assert_flat("/api/files/", self.make_file)

    def test_folders_list(self):
        def build():
            folder = Folder.objects.create(
                user=self.user, name=f"folder-{Folder.objects.count()}"
            )
            folder.files.add(self.make_file(), self.make_file())

        self.assert_flat("/api/folders/", build)

    def test_conversations_list(self):
        def build():
            conversation = Conversation.active_objects.create(
                user=self.user, title="c", prompt=self.make_prompt()
            )
            conversation.selected_mcp_servers.add(self.server)
            conversation.selected_dare_tools.add(self.tool)

        self.assert_flat("/api/conversations/", build)

    def test_conversation_messages(self):
        conversation = Conversation.active_objects.create(user=self.user, title="c")

        def build():
            message = Message.active_objects.create(
                conversation=conversation,
                sender_type=SenderType.AI_ASSISTANT,
                message="hello",
            )
            Artifact.active_objects.create(
                conversation=conversation, message=message, title="a"
            )

        self.assert_flat(
            f"/api/conversations/{conversation.conversation_id}/messages/", build
        )
