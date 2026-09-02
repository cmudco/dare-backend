from unittest.mock import AsyncMock, patch

from django.test import TestCase

from core.services.background_model_service import BackgroundModelResult
from core.services.rag.query_analyzer import QueryAnalyzer, QueryPlanResponse
from users.models import User


class QueryAnalyzerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="rag@example.com", password="x")
        self.response = QueryPlanResponse(
            intent="exploratory",
            keywords=["widows", "dependency"],
            rewritten_query="How did widows prove dependency?",
            hyde_passage="Widows submitted affidavits.",
        )

    def test_returns_the_typed_plan_from_the_background_model(self):
        parse = AsyncMock(
            return_value=BackgroundModelResult(
                value=self.response,
                route=None,
                input_tokens=20,
                output_tokens=10,
            )
        )
        with patch(
            "core.services.rag.query_analyzer.BackgroundModelService.parse_structured",
            new=parse,
        ):
            plan = QueryAnalyzer().analyze(
                "how did widows prove dependency", self.user.pk
            )

        self.assertEqual(plan.intent, "exploratory")
        self.assertEqual(plan.keywords, ("widows", "dependency"))
        self.assertTrue(plan.is_exploratory)
        self.assertIs(parse.await_args.kwargs["response_model"], QueryPlanResponse)

    def test_a_failing_call_falls_back_to_the_raw_query(self):
        with patch(
            "core.services.rag.query_analyzer.BackgroundModelService.parse_structured",
            new=AsyncMock(side_effect=RuntimeError("gateway down")),
        ):
            analyzer = QueryAnalyzer()
            self.assertIsNone(analyzer.analyze("anything", self.user.pk))
            self.assertIn("gateway down", analyzer.last_error)

    def test_missing_payer_skips_the_paid_analysis_call(self):
        parse = AsyncMock()
        with patch(
            "core.services.rag.query_analyzer.BackgroundModelService.parse_structured",
            new=parse,
        ):
            analyzer = QueryAnalyzer()
            self.assertIsNone(analyzer.analyze("anything"))

        parse.assert_not_awaited()
        self.assertIn("billing user", analyzer.last_error)

    def test_public_bot_id_is_forwarded_to_the_background_service(self):
        parse = AsyncMock(
            return_value=BackgroundModelResult(
                value=self.response,
                route=None,
                input_tokens=20,
                output_tokens=10,
            )
        )
        with patch(
            "core.services.rag.query_analyzer.BackgroundModelService.parse_structured",
            new=parse,
        ):
            plan = QueryAnalyzer().analyze("anything", payer_bot_id=42)

        self.assertIsNotNone(plan)
        self.assertIsNone(parse.await_args.kwargs["user"])
        self.assertEqual(parse.await_args.kwargs["public_bot_id"], 42)

    def test_empty_query_makes_no_request(self):
        parse = AsyncMock()
        with patch(
            "core.services.rag.query_analyzer.BackgroundModelService.parse_structured",
            new=parse,
        ):
            self.assertIsNone(QueryAnalyzer().analyze("", self.user.pk))

        parse.assert_not_awaited()
