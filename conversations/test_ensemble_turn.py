"""Boundary and policy decisions behind a panel/council turn."""

from django.test import SimpleTestCase, TestCase

from conversations.services.ensemble_service import (
    EnsembleTurn,
    _parse_evaluation,
    ensemble_enabled_for,
)
from core.services.dtos.builder import ARTIFACT_TOOL_SLUGS
from core.services.dtos.ensemble_dto import EnsembleRequest
from feature_flags.models import FeatureFlag, UserFeatureOverride
from users.models import User


class EnsembleRequestTests(SimpleTestCase):
    def test_parses_a_valid_panel(self):
        request = EnsembleRequest.parse(
            {"depth": "panel", "responder_ids": [29, "31"], "chairman_id": 29}
        )
        self.assertEqual(request.depth, "panel")
        self.assertEqual(request.responder_ids, ("29", "31"))
        self.assertEqual(request.chairman_id, "29")

    def test_rejects_single_model_and_unknown_depth(self):
        self.assertIsNone(EnsembleRequest.parse(None))
        self.assertIsNone(
            EnsembleRequest.parse(
                {"depth": "panel", "responder_ids": [29], "chairman_id": 29}
            )
        )
        self.assertIsNone(
            EnsembleRequest.parse(
                {"depth": "jury", "responder_ids": [29, 31], "chairman_id": 29}
            )
        )


class RolePolicyTests(SimpleTestCase):
    def _turn(self):
        return EnsembleTurn(
            message_data={
                "message": "q",
                "ensemble": object(),
                "artifacts_enabled": True,
                "web_search_enabled": True,
                "mcp_server_ids": [7],
                "dare_tool_slugs": ["create_chart", "search_documents"],
                "use_memory": True,
            },
            user_message="q",
            conversation=None,
            user=None,
            platform=None,
        )

    def test_responders_keep_tools_but_never_create_artifacts(self):
        data = self._turn().message_data_for("responder-1")
        self.assertFalse(data["artifacts_enabled"])
        self.assertTrue(data["web_search_enabled"])
        self.assertEqual(data["dare_tool_slugs"], ["search_documents"])
        self.assertNotIn("ensemble", data)

    def test_evaluators_judge_on_shared_evidence_only(self):
        data = self._turn().message_data_for("evaluator-2")
        self.assertFalse(data["web_search_enabled"])
        self.assertEqual(data["mcp_server_ids"], [])
        self.assertEqual(data["dare_tool_slugs"], [])
        self.assertFalse(data["use_memory"])

    def test_chairman_owns_artifacts_and_nothing_else(self):
        data = self._turn().message_data_for("chairman")
        self.assertTrue(data["artifacts_enabled"])
        self.assertFalse(data["web_search_enabled"])
        self.assertEqual(data["mcp_server_ids"], [])
        self.assertTrue(set(data["dare_tool_slugs"]) <= ARTIFACT_TOOL_SLUGS)


class EvaluationParsingTests(SimpleTestCase):
    def test_reads_json_with_or_without_a_fence(self):
        ranking, notes = _parse_evaluation(
            '```json\n{"ranking": ["B", "A"], "notes": "B cites sources."}\n```'
        )
        self.assertEqual(ranking, ["B", "A"])
        self.assertEqual(notes, "B cites sources.")

    def test_prose_falls_back_to_no_ranking(self):
        ranking, notes = _parse_evaluation("I prefer the second draft.")
        self.assertEqual(ranking, [])
        self.assertEqual(notes, "I prefer the second draft.")


class EnsembleFlagGateTests(TestCase):
    """Panel/council turns are allowed only when the user's flag is on."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="ensemble-gate@example.com", password="password"
        )
        self.flag = FeatureFlag.objects.get(key="enable_ensemble")

    def test_off_by_default(self):
        self.assertFalse(ensemble_enabled_for(self.user))
        self.assertFalse(ensemble_enabled_for(None))

    def test_on_with_a_user_override(self):
        UserFeatureOverride.objects.create(flag=self.flag, user=self.user, enabled=True)
        self.assertTrue(ensemble_enabled_for(self.user))
