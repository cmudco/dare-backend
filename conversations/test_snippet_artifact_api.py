from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import Conversation, Message

User = get_user_model()

COMPONENT = "export default function Counter() {\n  return <div />\n}"


class SnippetArtifactApiTests(TestCase):
    """Opening a chat code block as an artifact, then editing it."""

    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="pw")
        self.conversation = Conversation.active_objects.create(user=self.owner)
        self.message = Message.active_objects.create(
            conversation=self.conversation,
            sender_type=2,
            sender="AI Assistant",
            message=f"```jsx\n{COMPONENT}\n```",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/conversations/{self.conversation.conversation_id}/artifacts/"
        self.body = {
            "messageId": self.message.id,
            "language": "jsx",
            "content": COMPONENT,
        }

    def edit(self, artifact_id, content):
        return self.client.patch(
            f"/api/artifacts/{artifact_id}/content/", {"content": content}
        )

    def test_reopening_a_block_returns_its_latest_version(self):
        first = self.client.post(self.url, self.body, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.data["artifact_type"], "react")
        self.assertEqual(first.data["title"], "Counter")

        edited = self.edit(first.data["id"], COMPONENT.replace("div", "span"))
        self.assertEqual(edited.data["version"], 2)
        self.assertEqual(edited.data["filename"], "Counter.jsx")

        again = self.client.post(self.url, self.body, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], edited.data["id"])

    def test_editing_an_older_version_appends_after_the_newest(self):
        v1 = self.client.post(self.url, self.body, format="json").data
        self.edit(v1["id"], "v2")
        v3 = self.edit(v1["id"], "v3").data
        self.assertEqual(v3["version"], 3)

    def test_other_users_cannot_open_blocks_in_the_conversation(self):
        stranger = User.objects.create_user(email="other@example.com", password="pw")
        self.client.force_authenticate(stranger)
        response = self.client.post(self.url, self.body, format="json")
        self.assertEqual(response.status_code, 404)
