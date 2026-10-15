"""Ask DARE setting up agents: prompt and model lookups, create_agent proposals."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from agents.models import Agent
from assistant.constants import (
    LIST_MY_MODELS,
    LIST_MY_PROMPTS,
    PROPOSE_CHANGES,
    ProposalStatus,
)
from assistant.models import AssistantProposal
from assistant.services.account_tools import execute_account_tool
from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from conversations.models import LLM, Conversation
from conversations.services.llm_filter_service import WalletMeta, _litellm_entry
from prompts.models import Prompt

GATEWAY_MODEL = "gemini/gemini-3.8-flash"


class AgentBuilderTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="builder@example.com", password="x")
        cls.other = users.create_user(email="builder-other@example.com", password="x")
        cls.seeing = LLM.objects.create(
            name="Seeing Model",
            identifier="seeing-model",
            provider="openai",
            supports_vision=True,
            supports_temperature=False,
            supports_effort=True,
        )
        cls.painter = LLM.objects.create(
            name="Painter",
            identifier="painter-model",
            provider="openai",
            is_image_generator=True,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.prompt = Prompt.active_objects.create(
            user=self.user, title="A2 Parametric Span", content="Help with GHPython."
        )

    def tool(self, name, arguments=None, user=None):
        return execute_account_tool(name, arguments or {}, user or self.user)

    def propose_agent(self, agent, name="A2 helper"):
        return self.tool(
            PROPOSE_CHANGES,
            {
                "summary": "Set up an A2 agent",
                "actions": [{"type": "create_agent", "name": name, "agent": agent}],
            },
        )

    def post(self, proposal_id, verb):
        return self.client.post(
            f"/api/assistant/proposals/{proposal_id}/{verb}/", {}, format="json"
        )


class GatewayRosterMixin:
    """Puts the user on a LiteLLM key that serves one Gemini model."""

    def setUp(self):
        super().setUp()
        self.key = LiteLLMKey.objects.create(
            label="course gateway",
            base_url="https://gateway.example/v1",
            api_key="key",
            source=LiteLLMKeySourceChoice.USER,
            owner_user=self.user,
            created_by=self.user,
        )
        self.gateway_ref = f"litellm:{self.key.pk}:{GATEWAY_MODEL}"
        roster = patch(
            "assistant.services.agent_builder.filter_for_active_wallet",
            return_value=(
                [_litellm_entry(self.key, GATEWAY_MODEL)],
                WalletMeta(type=UserWalletPreferenceTypeChoice.LITELLM),
            ),
        )
        roster.start()
        self.addCleanup(roster.stop)


class ListToolTests(AgentBuilderTestCase):
    def test_models_list_chat_models_with_their_capabilities(self):
        models = {m["name"]: m for m in self.tool(LIST_MY_MODELS)["models"]}

        seeing = models["Seeing Model"]
        self.assertEqual(seeing["model_ref"], str(self.seeing.pk))
        self.assertTrue(seeing["accepts_images"])
        self.assertFalse(seeing["temperature"])
        self.assertTrue(seeing["effort"])
        self.assertNotIn("Painter", models)

    def test_prompts_list_the_newest_version_of_the_users_own(self):
        newer = Prompt.active_objects.create(
            user=self.user, title="A2 Parametric Span", content="v2", parent=self.prompt
        )
        Prompt.active_objects.create(user=self.other, title="Theirs", content="x")

        prompts = self.tool(LIST_MY_PROMPTS)["prompts"]

        self.assertEqual([p["id"] for p in prompts], [newer.id])

    def test_prompts_can_be_filtered_by_title(self):
        Prompt.active_objects.create(user=self.user, title="Essay coach", content="x")

        prompts = self.tool(LIST_MY_PROMPTS, {"title_contains": "span"})["prompts"]

        self.assertEqual([p["title"] for p in prompts], ["A2 Parametric Span"])


class GatewayListToolTests(GatewayRosterMixin, AgentBuilderTestCase):
    def test_gateway_models_report_images_and_web_search(self):
        (model,) = self.tool(LIST_MY_MODELS)["models"]

        self.assertEqual(model["model_ref"], self.gateway_ref)
        self.assertTrue(model["accepts_images"])
        self.assertTrue(model["web_search"])
        self.assertFalse(model["web_fetch"])


class CreateAgentTests(GatewayRosterMixin, AgentBuilderTestCase):
    def agent_spec(self, **overrides):
        return {
            "description": "Coaches GHPython for A2.",
            "model_ref": self.gateway_ref,
            "prompt_id": self.prompt.id,
            "enable_web_search": True,
            **overrides,
        }

    def test_apply_creates_the_agent_and_undo_removes_it(self):
        proposal_id = self.propose_agent(self.agent_spec())["proposal_id"]
        self.assertFalse(Agent.active_objects.exists())

        applied = self.post(proposal_id, "apply").json()

        self.assertEqual(applied["status"], ProposalStatus.APPLIED)
        shown = applied["actions"][0]["agent"]
        self.assertEqual(shown["modelName"], GATEWAY_MODEL)
        self.assertEqual(shown["promptTitle"], "A2 Parametric Span")
        agent = Agent.active_objects.get(user=self.user)
        self.assertEqual(agent.name, "A2 helper")
        self.assertEqual(agent.litellm_key_id, self.key.pk)
        self.assertEqual(agent.litellm_model_name, GATEWAY_MODEL)
        self.assertEqual(agent.prompt_id, self.prompt.id)
        self.assertTrue(agent.enable_web_search)

        self.post(proposal_id, "undo")
        self.assertFalse(Agent.active_objects.exists())
        self.prompt.refresh_from_db()
        self.assertFalse(self.prompt.is_deleted)

        self.post(proposal_id, "apply")
        self.assertEqual(Agent.active_objects.filter(user=self.user).count(), 1)

    def test_a_new_prompt_is_created_with_the_agent_and_undone_with_it(self):
        spec = self.agent_spec(
            prompt_id=None, new_prompt={"title": "Essay coach", "content": "Coach."}
        )
        spec.pop("prompt_id")
        proposal_id = self.propose_agent(spec)["proposal_id"]

        self.post(proposal_id, "apply")
        prompt = Prompt.active_objects.get(user=self.user, title="Essay coach")
        self.assertEqual(Agent.active_objects.get(user=self.user).prompt, prompt)

        self.post(proposal_id, "undo")
        self.assertFalse(Prompt.active_objects.filter(pk=prompt.pk).exists())

    def test_undo_keeps_a_new_prompt_that_a_chat_now_uses(self):
        spec = self.agent_spec(new_prompt={"title": "Essay coach", "content": "C."})
        spec.pop("prompt_id")
        proposal_id = self.propose_agent(spec)["proposal_id"]
        self.post(proposal_id, "apply")
        prompt = Prompt.active_objects.get(user=self.user, title="Essay coach")
        Conversation.active_objects.create(user=self.user, title="T", prompt=prompt)

        notes = self.post(proposal_id, "undo").json()["actions"][0]["notes"]

        self.assertTrue(Prompt.active_objects.filter(pk=prompt.pk).exists())
        self.assertIn("kept", notes[0])

    def test_plans_the_user_cannot_carry_are_rejected_with_readable_errors(self):
        theirs = Prompt.active_objects.create(user=self.other, title="T", content="x")
        cases = {
            "Unknown model_ref": self.agent_spec(model_ref="litellm:nope:gpt-6"),
            "Unknown prompt id": self.agent_spec(prompt_id=theirs.id),
            "cannot fetch pages": self.agent_spec(web_fetch_enabled=True),
            "exactly one of": self.agent_spec(
                new_prompt={"title": "x", "content": "y"}
            ),
            "between 0.0 and 1.0": self.agent_spec(temperature=1.5),
        }
        for expected, spec in cases.items():
            with self.subTest(expected):
                self.assertIn(expected, self.propose_agent(spec)["error"])
        self.assertIn("needs an `agent`", self.propose_agent(None)["error"])
        self.assertFalse(AssistantProposal.objects.exists())


class DareModelCapabilityTests(AgentBuilderTestCase):
    def test_settings_the_model_does_not_take_are_dropped(self):
        spec = {
            "model_ref": str(self.seeing.pk),
            "prompt_id": self.prompt.id,
            "temperature": 0.3,
            "effort": "low",
        }

        proposal_id = self.propose_agent(spec)["proposal_id"]

        agent = AssistantProposal.objects.get(pk=proposal_id).plan["actions"][0][
            "agent"
        ]
        self.assertIsNone(agent["temperature"])
        self.assertEqual(agent["effort"], "low")

    def test_an_empty_new_prompt_beside_a_prompt_id_is_ignored(self):
        spec = {
            "model_ref": str(self.seeing.pk),
            "prompt_id": self.prompt.id,
            "new_prompt": {"title": "", "content": ""},
        }

        proposal_id = self.propose_agent(spec)["proposal_id"]

        agent = AssistantProposal.objects.get(pk=proposal_id).plan["actions"][0][
            "agent"
        ]
        self.assertIsNone(agent["new_prompt"])
        self.assertEqual(agent["prompt_title"], "A2 Parametric Span")
