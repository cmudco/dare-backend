"""All Socratic models have headroom without raising regular chat limits."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from conversations.services import message_validation_service
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.gemini_service import GeminiService
from users.constants import AuthSourceChoice


class SocraticTokenBudgetTests(SimpleTestCase):
    def request(
        self, *, reasoning=True, platform=AuthSourceChoice.SOCRATIC_BOTS, **payload
    ):
        llm = SimpleNamespace(
            identifier="gemini-3.8-flash",
            provider="gemini",
            is_reasoning=reasoning,
            supports_effort=True,
            default_effort="medium",
            supports_temperature=True,
        )
        return LLMQueryRequestBuilder.from_message_data(
            message="List the transcript",
            user=None,
            llm=llm,
            platform=platform,
            message_data=message_validation_service.MessageValidationService.validate_and_parse(
                {"message": "List the transcript", **payload}
            ),
        )

    def test_reasoning_budget_applies_to_both_socratic_modes(self):
        for advanced in (True, False):
            request = self.request(is_advanced=advanced)
            self.assertEqual(request.generation.max_tokens, 32000)
            service = GeminiService(llm=request.llm, api_key="test-key")
            config = service._build_generation_config(
                request.generation.max_tokens, 0.7, None
            )
            self.assertEqual(config.max_output_tokens, 35000)

    def test_higher_explicit_allowance_is_preserved(self):
        self.assertEqual(self.request(max_tokens=40000).generation.max_tokens, 40000)

    def test_non_reasoning_socratic_uses_same_floor(self):
        self.assertEqual(self.request(reasoning=False).generation.max_tokens, 32000)

    def test_regular_chat_retains_default_and_explicit_allowances(self):
        self.assertEqual(self.request(platform=None).generation.max_tokens, 2048)
        self.assertEqual(
            self.request(platform=None, max_tokens=6000).generation.max_tokens, 6000
        )
