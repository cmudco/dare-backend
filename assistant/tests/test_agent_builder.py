"""Ask DARE setting up agents: suggesting a model and web search, never a prompt."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from agents.models import Agent
from assistant.constants import (
    LIST_MY_MODELS,
    PROPOSE_CHANGES,
    SEARCH_MY_PROMPTS,
    ProposalStatus,
)
from assistant.models import AssistantProposal
from assistant.services.account_tools import execute_account_tool
from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from conversations.models import LLM
from conversations.services.llm_filter_service import WalletMeta, _litellm_entry
from core.services.reference_pricing import ReferenceRates
from prompts.models import Prompt

# Gateway models with registry output prices (USD per million tokens).
GATEWAY_PRICES = {
    "gpt-5.5": "30",
    "gpt-6.1-sol": "10",
    "gpt-6-luna": "0.5",
    "gemini/gemini-3.8-flash": "3.75",
    "gemini/gemini-3.5-flash-lite": "2.5",
    "us.meta.llama3-1-8b-instruct-v1:0": None,
    "text-embedding-3-small": "0.02",
    "gpt-4o-transcribe": "10",
}


def registry_rates(model_name):
    rate = GATEWAY_PRICES.get(model_name)
    if rate is None:
        return None
    return ReferenceRates(Decimal(rate) / 5, Decimal(rate))


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
                [_litellm_entry(self.key, model) for model in GATEWAY_PRICES],
                WalletMeta(type=UserWalletPreferenceTypeChoice.LITELLM),
            ),
        )
        roster.start()
        self.addCleanup(roster.stop)
        prices = patch(
            "assistant.services.agent_builder.reference_rates",
            side_effect=registry_rates,
        )
        prices.start()
        self.addCleanup(prices.stop)

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

    def post(self, proposal_id, verb, body=None):
        return self.client.post(
            f"/api/assistant/proposals/{proposal_id}/{verb}/",
            body or {},
            format="json",
        )


class ListModelsTests(AgentBuilderTestCase):
    def test_chat_models_with_quality_balanced_and_budget_picks_first(self):
        models = execute_account_tool(LIST_MY_MODELS, {}, self.user)["models"]

        self.assertEqual(
            [(m["name"], m["recommended_as"]) for m in models[:3]],
            [
                ("gpt-6.1-sol", "quality"),
                ("gemini/gemini-3.8-flash", "balanced"),
                ("gpt-6-luna", "budget"),
            ],
        )
        self.assertFalse(any(m["recommended_as"] for m in models[3:]))
        names = [m["name"] for m in models]
        self.assertNotIn("text-embedding-3-small", names)
        self.assertNotIn("gpt-4o-transcribe", names)
        self.assertEqual(models[0]["output_usd_per_million_tokens"], "10")
        self.assertTrue(models[0]["accepts_images"] and models[0]["web_search"])

    @override_settings(ASSISTANT_AGENT_MAX_OUTPUT_RATE=Decimal("5"))
    def test_a_lower_ceiling_drops_the_quality_pick(self):
        models = execute_account_tool(LIST_MY_MODELS, {}, self.user)["models"]

        roles = [m["recommended_as"] for m in models if m["recommended_as"]]
        self.assertEqual(roles, ["balanced", "budget"])


class CreateAgentTests(AgentBuilderTestCase):
    def propose_options(self):
        return self.propose_agent(
            {
                "options": [
                    {"model_ref": self.ref(model), "why": f"{model} suits GHPython."}
                    for model in (
                        "gemini/gemini-3.8-flash",
                        "gpt-6.1-sol",
                        "gpt-6-luna",
                    )
                ],
                "enable_web_search": True,
                "web_search_why": "Looks up the official Grasshopper docs.",
                "description": "GHPython help for A2.",
            }
        )["proposal_id"]

    def test_the_card_offers_each_model_with_its_role_and_price(self):
        proposal_id = self.propose_options()

        stored = AssistantProposal.objects.get(pk=proposal_id).plan["actions"][0]
        self.assertEqual(
            [
                (o["model_name"], o["role"], o["output_usd_per_million_tokens"])
                for o in stored["agent"]["options"]
            ],
            [
                ("gemini/gemini-3.8-flash", "balanced", "3.75"),
                ("gpt-6.1-sol", "quality", "10"),
                ("gpt-6-luna", "budget", "0.5"),
            ],
        )
        self.assertIsNone(stored["agent"]["selected_model_ref"])

    def test_apply_uses_the_picked_model_and_undo_clears_the_pick(self):
        proposal_id = self.propose_options()

        applied = self.post(
            proposal_id,
            "apply",
            {"modelChoices": {"1": self.ref("gpt-6-luna")}},
        ).json()

        self.assertEqual(applied["status"], ProposalStatus.APPLIED)
        self.assertEqual(
            applied["actions"][0]["agent"]["selectedModelRef"], self.ref("gpt-6-luna")
        )
        agent = Agent.active_objects.get(user=self.user)
        self.assertEqual(agent.litellm_model_name, "gpt-6-luna")
        self.assertIsNone(agent.prompt_id)
        self.assertTrue(agent.enable_web_search)

        undone = self.post(proposal_id, "undo").json()
        self.assertFalse(Agent.active_objects.exists())
        self.assertIsNone(undone["actions"][0]["agent"]["selectedModelRef"])

    def test_apply_without_a_pick_uses_the_first_option(self):
        proposal_id = self.propose_options()

        self.post(proposal_id, "apply")

        agent = Agent.active_objects.get(user=self.user)
        self.assertEqual(agent.litellm_model_name, "gemini/gemini-3.8-flash")

    def test_a_model_that_was_not_offered_cannot_be_picked(self):
        proposal_id = self.propose_options()

        response = self.post(
            proposal_id, "apply", {"modelChoices": {"1": self.ref("gpt-5.5")}}
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(Agent.active_objects.exists())

    def test_plans_the_user_cannot_carry_are_rejected_with_readable_errors(self):
        cases = {
            "Unknown model_refs": {
                "options": [{"model_ref": "litellm:nope:gpt-6", "why": "x"}],
                "enable_web_search": False,
            },
            "cannot search the web": {
                "options": [
                    {
                        "model_ref": self.ref("us.meta.llama3-1-8b-instruct-v1:0"),
                        "why": "x",
                    }
                ],
                "enable_web_search": True,
            },
            "needs agent.options": {"options": [], "enable_web_search": False},
            "one-sentence `why`": {
                "options": [{"model_ref": self.ref("gpt-6-luna"), "why": ""}],
                "enable_web_search": False,
            },
        }
        for expected, agent in cases.items():
            with self.subTest(expected):
                self.assertIn(expected, self.propose_agent(agent)["error"])
        self.assertIn("needs an `agent`", self.propose_agent(None)["error"])
        self.assertFalse(AssistantProposal.objects.exists())


class PromptSuggestionTests(AgentBuilderTestCase):
    def setUp(self):
        super().setUp()
        self.a2 = Prompt.active_objects.create(
            user=self.user,
            title="A2 Parametric Span coach",
            content="Help students with GHPython in Grasshopper.",
        )
        Prompt.active_objects.create(
            user=self.user, title="Essay coach", content="Mentions Grasshopper once."
        )
        other = get_user_model().objects.create_user(email="other-prompts@example.com")
        Prompt.active_objects.create(
            user=other, title="A2 Parametric Span (theirs)", content="x"
        )

    def search(self, query):
        return execute_account_tool(SEARCH_MY_PROMPTS, {"query": query}, self.user)

    def test_title_matches_rank_first_and_other_users_prompts_never_appear(self):
        prompts = self.search("A2 Parametric Span Grasshopper")["prompts"]

        self.assertEqual(
            [p["title"] for p in prompts], ["A2 Parametric Span coach", "Essay coach"]
        )

    def test_only_the_newest_version_of_a_prompt_is_found(self):
        newer = Prompt.active_objects.create(
            user=self.user,
            title="A2 Parametric Span coach",
            content="v2",
            parent=self.a2,
        )

        ids = [p["prompt_id"] for p in self.search("parametric")["prompts"]]

        self.assertEqual(ids, [newer.id])

    def test_a_query_without_keywords_is_rejected(self):
        self.assertIn("keywords", self.search("a b")["error"])

    def propose(self, prompt):
        return self.propose_agent(
            {
                "options": [{"model_ref": self.ref("gpt-6-luna"), "why": "Cheap."}],
                "enable_web_search": False,
                "prompt": prompt,
            }
        )

    def test_a_suggested_prompt_is_shown_and_set_on_apply(self):
        proposal_id = self.propose(
            {"prompt_id": self.a2.id, "why": "It is the A2 course prompt."}
        )["proposal_id"]

        applied = self.post(proposal_id, "apply").json()

        self.assertEqual(
            applied["actions"][0]["agent"]["prompt"],
            {
                "id": self.a2.id,
                "title": "A2 Parametric Span coach",
                "why": "It is the A2 course prompt.",
            },
        )
        self.assertEqual(Agent.active_objects.get(user=self.user).prompt, self.a2)

    def test_a_blank_suggestion_means_no_prompt(self):
        proposal_id = self.propose({"prompt_id": 0, "why": ""})["proposal_id"]

        self.post(proposal_id, "apply")

        self.assertIsNone(Agent.active_objects.get(user=self.user).prompt_id)

    def test_another_users_prompt_cannot_be_suggested(self):
        theirs = Prompt.active_objects.exclude(user=self.user).get()

        result = self.propose({"prompt_id": theirs.id, "why": "x"})

        self.assertIn("Unknown prompt_id", result["error"])


class DareWalletTests(TestCase):
    """DARE models are priced from their own rows, not the registry."""

    def test_dare_models_are_ranked_by_their_database_price(self):
        user = get_user_model().objects.create_user(email="dare@example.com")
        LLM.objects.filter(is_active=True).update(is_active=False)
        affordable = LLM.objects.create(
            name="GPT-8 Sol",
            identifier="gpt-8-sol",
            provider="openai",
            supports_vision=True,
            output_token_rate_per_million=Decimal("8"),
        )
        LLM.objects.create(
            name="GPT-9 Sol",
            identifier="gpt-9-sol",
            provider="openai",
            output_token_rate_per_million=Decimal("60"),
        )

        models = execute_account_tool(LIST_MY_MODELS, {}, user)["models"]

        self.assertEqual(
            [
                (m["model_ref"], m["recommended_as"])
                for m in models
                if m["recommended_as"]
            ],
            [(str(affordable.pk), "quality")],
        )
        self.assertTrue(models[0]["accepts_images"])
