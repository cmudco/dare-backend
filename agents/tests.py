"""Agents: model choice (DARE or gateway), full chat setup, versions, applying to chats."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from agents.models import Agent
from billing.constants import LiteLLMKeySourceChoice
from billing.models import LiteLLMKey
from conversations.models import LLM, Conversation
from files.models import File
from prompts.models import Prompt

AGENTS_URL = "/api/agents/"
CONVERSATIONS_URL = "/api/conversations/"
GATEWAY_MODEL = "gemini/gemini-3.8-flash"


class AgentTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="agents-owner@example.com", password="x")
        cls.other = users.create_user(email="agents-other@example.com", password="x")
        cls.model = LLM.objects.filter(is_active=True).first()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.prompt = Prompt.active_objects.create(
            user=self.user, title="A2 coach", content="Help with GHPython."
        )
        self.key = self.make_key(self.user)
        self.gateway_ref = f"litellm:{self.key.pk}:{GATEWAY_MODEL}"

    def make_key(self, user):
        return LiteLLMKey.objects.create(
            label="course gateway",
            base_url="https://gateway.example/v1",
            api_key="key",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=user,
            created_by=user,
        )

    def make_file(self, user, name):
        return File.active_objects.create(user=user, file=f"files/{name}", name=name)

    def create(self, **fields):
        payload = {"name": "A2 helper", "prompt": self.prompt.id, **fields}
        return self.client.post(AGENTS_URL, payload, format="json")


class AgentModelTests(AgentTestCase):
    def test_an_agent_can_use_a_gateway_model(self):
        response = self.create(modelRef=self.gateway_ref)

        self.assertEqual(response.status_code, 201)
        agent = Agent.active_objects.get(pk=response.data["id"])
        self.assertEqual(agent.litellm_key_id, self.key.pk)
        self.assertEqual(agent.litellm_model_name, GATEWAY_MODEL)
        self.assertIsNone(agent.llm_id)
        self.assertEqual(response.data["model_ref"], self.gateway_ref)
        self.assertEqual(response.data["model_name"], GATEWAY_MODEL)

    def test_an_agent_can_use_a_dare_model(self):
        response = self.create(modelRef=str(self.model.pk))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["model_ref"], str(self.model.pk))
        self.assertEqual(response.data["model_name"], self.model.name)

    def test_switching_to_a_dare_model_clears_the_gateway_choice(self):
        agent_id = self.create(modelRef=self.gateway_ref).data["id"]

        self.client.patch(
            f"{AGENTS_URL}{agent_id}/simple-update/",
            {"modelRef": str(self.model.pk)},
            format="json",
        )

        agent = Agent.active_objects.get(pk=agent_id)
        self.assertEqual(agent.llm_id, self.model.pk)
        self.assertIsNone(agent.litellm_key_id)
        self.assertEqual(agent.litellm_model_name, "")

    def test_another_users_key_is_rejected(self):
        theirs = self.make_key(self.other)

        response = self.create(modelRef=f"litellm:{theirs.pk}:{GATEWAY_MODEL}")

        self.assertEqual(response.status_code, 400)
        self.assertIn("model_ref", response.data)


class PromptlessAgentTests(AgentTestCase):
    def test_an_agent_without_a_prompt_keeps_the_chats_prompt(self):
        agent_id = self.client.post(
            AGENTS_URL,
            {"name": "Model only", "modelRef": self.gateway_ref},
            format="json",
        ).data["id"]
        chat = Conversation.active_objects.create(
            user=self.user, title="A2", prompt=self.prompt
        )

        response = self.client.post(
            f"{CONVERSATIONS_URL}{chat.conversation_id}/apply-agent/",
            {"agent": agent_id},
            format="json",
        )

        self.assertEqual(response.data["selected_model_ref"], self.gateway_ref)
        chat.refresh_from_db()
        self.assertEqual(chat.prompt_id, self.prompt.id)


class AgentOwnershipTests(AgentTestCase):
    def test_another_users_prompt_is_rejected(self):
        theirs = Prompt.active_objects.create(user=self.other, title="T", content="x")

        response = self.create(prompt=theirs.id)

        self.assertEqual(response.status_code, 400)
        self.assertIn("prompt", response.data)

    def test_another_users_files_are_rejected(self):
        theirs = self.make_file(self.other, "theirs.pdf")

        response = self.create(contentFiles=[theirs.id])

        self.assertEqual(response.status_code, 400)

    def test_another_users_agent_is_404(self):
        theirs = Agent.active_objects.create(
            user=self.other, name="Theirs", prompt=self.prompt
        )

        response = self.client.get(f"{AGENTS_URL}{theirs.id}/")

        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_request_is_401(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(AGENTS_URL).status_code, 401)


class AgentVersionTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.notes = self.make_file(self.user, "notes.pdf")
        response = self.create(
            modelRef=self.gateway_ref,
            temperature=0.3,
            effort="low",
            historyLimit=12,
            ragMode="agentic",
            enableWebSearch=True,
            webFetchEnabled=True,
            artifactsEnabled=True,
            memoryEnabled=True,
            contentFiles=[self.notes.id],
        )
        self.agent = Agent.active_objects.get(pk=response.data["id"])

    def test_a_new_version_keeps_every_setting_it_does_not_change(self):
        response = self.client.put(
            f"{AGENTS_URL}{self.agent.id}/", {"temperature": 0.9}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        version = Agent.active_objects.get(pk=response.data["id"])
        self.assertEqual(version.parent_id, self.agent.id)
        self.assertEqual(version.version, 2)
        self.assertEqual(version.temperature, 0.9)
        self.assertEqual(response.data["model_ref"], self.gateway_ref)
        self.assertEqual(
            (version.effort, version.history_limit, version.rag_mode),
            ("low", 12, "agentic"),
        )
        self.assertTrue(
            version.enable_web_search
            and version.web_fetch_enabled
            and version.artifacts_enabled
            and version.memory_enabled
        )
        self.assertEqual(list(version.content_files.all()), [self.notes])
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.temperature, 0.3)

    def test_only_the_latest_version_can_be_edited(self):
        self.client.put(f"{AGENTS_URL}{self.agent.id}/", {"temperature": 0.9})

        response = self.client.put(
            f"{AGENTS_URL}{self.agent.id}/", {"temperature": 0.1}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_a_clone_copies_the_whole_setup(self):
        response = self.client.post(f"{AGENTS_URL}{self.agent.id}/clone/")

        self.assertEqual(response.status_code, 201)
        clone = Agent.active_objects.get(pk=response.data["id"])
        self.assertIsNone(clone.parent_id)
        self.assertEqual(response.data["model_ref"], self.gateway_ref)
        self.assertEqual(clone.history_limit, 12)
        self.assertTrue(clone.memory_enabled)
        self.assertEqual(list(clone.content_files.all()), [self.notes])


class ApplyAgentTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        self.notes = self.make_file(self.user, "notes.pdf")
        self.chat = Conversation.active_objects.create(user=self.user, title="A2")
        self.url = f"{CONVERSATIONS_URL}{self.chat.conversation_id}/apply-agent/"

    def make_agent(self, **fields):
        response = self.create(
            temperature=0.2,
            effort="medium",
            historyLimit=8,
            enableWebSearch=True,
            artifactsEnabled=True,
            contentFiles=[self.notes.id],
            **fields,
        )
        return Agent.active_objects.get(pk=response.data["id"])

    def test_applying_a_gateway_agent_gives_the_chat_its_whole_setup(self):
        agent = self.make_agent(modelRef=self.gateway_ref)

        response = self.client.post(self.url, {"agent": agent.id}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["selected_agent"], agent.id)
        self.assertEqual(response.data["selected_model_ref"], self.gateway_ref)
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.prompt_id, self.prompt.id)
        self.assertIsNone(self.chat.selected_model_id)
        self.assertEqual(
            (self.chat.temperature, self.chat.effort, self.chat.history_limit),
            (0.2, "medium", 8),
        )
        self.assertTrue(self.chat.web_search_enabled and self.chat.artifacts_enabled)
        self.assertEqual(self.chat.selected_file_ids, [self.notes.id])

    def test_applying_a_dare_model_agent_selects_that_model(self):
        agent = self.make_agent(modelRef=str(self.model.pk))

        response = self.client.post(self.url, {"agent": agent.id}, format="json")

        self.assertEqual(response.data["selected_model_ref"], str(self.model.pk))

    def test_detaching_keeps_settings_and_drops_the_agents_sources(self):
        agent = self.make_agent(modelRef=self.gateway_ref)
        self.client.post(self.url, {"agent": agent.id}, format="json")

        response = self.client.post(self.url, {"agent": None}, format="json")

        self.assertEqual(response.status_code, 200)
        self.chat.refresh_from_db()
        self.assertIsNone(self.chat.selected_agent_id)
        self.assertEqual(self.chat.selected_file_ids, [])
        self.assertEqual(self.chat.temperature, 0.2)

    def test_another_users_agent_cannot_be_applied(self):
        theirs = Agent.active_objects.create(
            user=self.other, name="Theirs", prompt=self.prompt
        )

        response = self.client.post(self.url, {"agent": theirs.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.chat.refresh_from_db()
        self.assertIsNone(self.chat.selected_agent_id)

    def test_another_users_chat_is_404(self):
        agent = self.make_agent(modelRef=self.gateway_ref)
        theirs = Conversation.active_objects.create(user=self.other, title="T")

        response = self.client.post(
            f"{CONVERSATIONS_URL}{theirs.conversation_id}/apply-agent/",
            {"agent": agent.id},
            format="json",
        )

        self.assertEqual(response.status_code, 404)


class AgentListQueryCountTests(AgentTestCase):
    def add_agent(self):
        agent = Agent.active_objects.create(
            user=self.user, name="Helper", prompt=self.prompt, llm=self.model
        )
        agent.content_files.add(self.make_file(self.user, f"content-{agent.pk}.pdf"))
        agent.embedding_files.add(self.make_file(self.user, f"embed-{agent.pk}.pdf"))

    def count_list_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(AGENTS_URL)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_listing_agents_costs_the_same_queries_for_one_or_many(self):
        self.add_agent()
        few = self.count_list_queries()
        for _ in range(3):
            self.add_agent()

        self.assertEqual(self.count_list_queries(), few)
