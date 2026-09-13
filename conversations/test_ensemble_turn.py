"""Boundary and policy decisions behind a panel/council turn."""

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

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


class EnsembleBriefTests(SimpleTestCase):
    def _request(self, briefs):
        return EnsembleRequest.parse(
            {
                "depth": "panel",
                "responder_ids": [29, 31, 34],
                "chairman_id": 29,
                "briefs": briefs,
            }
        )

    def test_angles_align_with_the_line_up_and_blank_briefs_mean_default(self):
        request = self._request(
            {"responder": "  ", "chairman": "Fuse it.", "angles": ["Skeptic"]}
        )
        self.assertIsNone(request.briefs.responder)
        self.assertEqual(request.briefs.chairman, "Fuse it.")
        self.assertEqual(request.briefs.angles, ("Skeptic", "", ""))
        self.assertTrue(request.briefs.is_custom)
        self.assertFalse(self._request(None).briefs.is_custom)

    def test_seat_angle_is_appended_to_the_brief_that_applies(self):
        turn = EnsembleTurn(
            message_data={"message": "q"},
            user_message="q",
            conversation=None,
            user=None,
            platform=None,
            briefs=self._request(
                {"responder": "Be brief.", "angles": ["", "Lead with data"]}
            ).briefs,
        )
        self.assertEqual(turn.instructions_for("responder-1", "LIB"), "Be brief.")
        seat_two = turn.instructions_for("responder-2", "LIB")
        self.assertTrue(seat_two.startswith("Be brief.\n\n"))
        self.assertIn("Lead with data", seat_two)
        self.assertEqual(turn.instructions_for("chairman", "LIB"), "LIB")
        self.assertNotIn("Lead with data", turn.instructions_for("evaluator-2", "LIB"))


class EnsemblePresetApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="briefs@example.com", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_defaults_come_from_the_role_prompts(self):
        self.assertEqual(
            APIClient().get("/api/ensemble-presets/defaults/").status_code, 401
        )
        response = self.client.get("/api/ensemble-presets/defaults/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("panel", response.json()["responder"])
        self.assertIn("chairman", response.json()["chairman"])

    def test_presets_are_private_to_their_owner(self):
        created = self.client.post(
            "/api/ensemble-presets/",
            {"name": "Debate", "angles": ["For", "Against"]},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        other = User.objects.create_user(email="other@example.com", password="x")
        self.client.force_authenticate(other)
        self.assertEqual(
            self.client.get("/api/ensemble-presets/").json()["results"], []
        )
