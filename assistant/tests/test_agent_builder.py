"""Ask DARE setting up agents: suggesting a model and web search, never a prompt."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from agents.models import Agent
from assistant.constants import LIST_MY_MODELS, PROPOSE_CHANGES, ProposalStatus
from assistant.models import AssistantProposal
from assistant.services.account_tools import execute_account_tool
from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from conversations.models import LLM
from conversations.services.llm_filter_service import WalletMeta, _litellm_entry

GATEWAY_MODELS = (
    "gpt-5.5",
    "gpt-6.1-sol",
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.5-flash-lite",
    "us.meta.llama3-1-8b-instruct-v1:0",
    "text-embedding-3-small",
    "gpt-4o-transcribe",
)


class AgentBuilderTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="builder@example.com", password="x")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.key = LiteLLMKey.objects.create(
            label="course gateway",
            base_url="https://gateway.example/v1",
            api_key="key",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )
        roster = patch(
            "assistant.services.agent_builder.filter_for_active_wallet",
            return_value=(
                [_litellm_entry(self.key, model) for model in GATEWAY_MODELS],
                WalletMeta(type=UserWalletPreferenceTypeChoice.LITELLM),
            ),
        )
        roster.start()
        self.addCleanup(roster.stop)

    def ref(self, model):
        return f"litellm:{self.key.pk}:{model}"

    def propose_agent(self, agent, name="A2 helper"):
        return execute_account_tool(
            PROPOSE_CHANGES,
            {
                "summary": "Set up an A2 agent",
                "actions": [{"type": "create_agent", "name": name, "agent": agent}],
            },
            self.user,
        )

    def post(self, proposal_id, verb):
        return self.client.post(
            f"/api/assistant/proposals/{proposal_id}/{verb}/", {}, format="json"
        )


class ListModelsTests(AgentBuilderTestCase):
    def test_chat_models_only_with_the_newest_gpt_and_gemini_recommended_first(self):
        models = execute_account_tool(LIST_MY_MODELS, {}, self.user)["models"]

        names = [model["name"] for model in models]
        self.assertEqual(names[:2], ["gpt-6.1-sol", "gemini/gemini-3.8-flash"])
        self.assertTrue(all(model["recommended"] for model in models[:2]))
        self.assertFalse(any(model["recommended"] for model in models[2:]))
        self.assertNotIn("text-embedding-3-small", names)
        self.assertNotIn("gpt-4o-transcribe", names)
        self.assertTrue(models[0]["accepts_images"] and models[0]["web_search"])


class CreateAgentTests(AgentBuilderTestCase):
    def test_apply_creates_a_prompt_less_agent_and_undo_removes_it(self):
        proposal_id = self.propose_agent(
            {
                "model_ref": self.ref("gpt-6.1-sol"),
                "enable_web_search": True,
                "description": "GHPython help for A2.",
            }
        )["proposal_id"]

        applied = self.post(proposal_id, "apply").json()

        self.assertEqual(applied["status"], ProposalStatus.APPLIED)
        self.assertEqual(
            applied["actions"][0]["agent"],
            {
                "modelRef": self.ref("gpt-6.1-sol"),
                "modelName": "gpt-6.1-sol",
                "enableWebSearch": True,
            },
        )
        agent = Agent.active_objects.get(user=self.user)
        self.assertIsNone(agent.prompt_id)
        self.assertEqual(agent.litellm_model_name, "gpt-6.1-sol")
        self.assertTrue(agent.enable_web_search)
        self.assertEqual(agent.description, "GHPython help for A2.")

        self.post(proposal_id, "undo")
        self.assertFalse(Agent.active_objects.exists())

    def test_models_the_user_cannot_use_or_that_cannot_search_are_rejected(self):
        cases = {
            "Unknown model_ref": {
                "model_ref": "litellm:nope:gpt-6",
                "enable_web_search": False,
            },
            "cannot search the web": {
                "model_ref": self.ref("us.meta.llama3-1-8b-instruct-v1:0"),
                "enable_web_search": True,
            },
        }
        for expected, agent in cases.items():
            with self.subTest(expected):
                self.assertIn(expected, self.propose_agent(agent)["error"])
        self.assertIn("needs an `agent`", self.propose_agent(None)["error"])
        self.assertFalse(AssistantProposal.objects.exists())


class DareWalletTests(TestCase):
    def test_dare_models_report_images_from_the_catalogue(self):
        user = get_user_model().objects.create_user(email="dare@example.com")
        seeing = LLM.objects.create(
            name="Seeing Model",
            identifier="seeing-model",
            provider="openai",
            supports_vision=True,
        )

        models = execute_account_tool(LIST_MY_MODELS, {}, user)["models"]

        entry = next(m for m in models if m["model_ref"] == str(seeing.pk))
        self.assertTrue(entry["accepts_images"])
