import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
from django.test import SimpleTestCase

from core.services.rag.query_analyzer import QueryAnalyzer

# Captured before any patching: the stub below must not read the signature
# through the same module attribute the tests replace.
_REAL_CREATE_PARAMS = frozenset(
    inspect.signature(anthropic.Anthropic(api_key="x").messages.create).parameters
)

_PLAN = {
    "intent": "exploratory",
    "keywords": ["widows", "dependency"],
    "rewritten_query": "How did widows prove dependency?",
    "hyde_passage": "Widows submitted affidavits.",
}


class RecordingClient:
    """Stand-in for ``anthropic.Anthropic`` that rejects unknown kwargs.

    The real SDK raises TypeError on an argument its signature does not
    declare, which is what turned every RAG query into a Sentry event: the
    exception was caught, but Sentry's Anthropic integration had already
    recorded it.
    """

    def __init__(self, **_):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        unknown = set(kwargs) - _REAL_CREATE_PARAMS
        if unknown:
            raise TypeError(
                f"Messages.create() got an unexpected keyword argument "
                f"{sorted(unknown)[0]!r}"
            )
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(_PLAN))]
        )


class QueryAnalyzerTests(SimpleTestCase):
    def _analyze(self, query="how did widows prove dependency"):
        client = RecordingClient()
        with patch(
            "core.services.rag.query_analyzer.anthropic.Anthropic",
            return_value=client,
        ), patch(
            "core.services.rag.query_analyzer.get_provider_api_key_sync",
            return_value="test-key",
        ):
            return QueryAnalyzer().analyze(query), client

    def test_schema_rides_in_extra_body_not_as_a_keyword(self):
        # Passing it as a keyword is what the SDK rejects.
        _, client = self._analyze()

        sent = client.calls[0]
        self.assertNotIn("output_config", sent)
        self.assertEqual(
            sent["extra_body"]["output_config"]["format"]["type"], "json_schema"
        )

    def test_one_request_per_query(self):
        # Two means the old probe-then-retry is back.
        _, client = self._analyze()

        self.assertEqual(len(client.calls), 1)

    def test_returns_the_parsed_plan(self):
        plan, _ = self._analyze()

        self.assertEqual(plan.intent, "exploratory")
        self.assertEqual(plan.keywords, ("widows", "dependency"))
        self.assertTrue(plan.is_exploratory)

    def test_a_failing_call_falls_back_to_the_raw_query(self):
        with patch(
            "core.services.rag.query_analyzer.anthropic.Anthropic",
            side_effect=RuntimeError("gateway down"),
        ), patch(
            "core.services.rag.query_analyzer.get_provider_api_key_sync",
            return_value="test-key",
        ):
            analyzer = QueryAnalyzer()
            self.assertIsNone(analyzer.analyze("anything"))
            self.assertIn("gateway down", analyzer.last_error)

    def test_empty_query_makes_no_request(self):
        plan, client = self._analyze(query="")

        self.assertIsNone(plan)
        self.assertEqual(client.calls, [])
