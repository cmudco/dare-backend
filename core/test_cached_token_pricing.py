from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from conversations.services.message_helpers.usage_helpers import (
    UsageAccumulator,
    estimate_usage,
)
from core.services.billing_service import BillingService
from core.services.llm_utils.usage_extractors import (
    ClaudeUsageExtractor,
    OpenAIUsageExtractor,
)


def _llm(input_rate, output_rate, cached_rate):
    return SimpleNamespace(
        input_token_rate_per_million=Decimal(input_rate),
        output_token_rate_per_million=Decimal(output_rate),
        cached_input_token_rate_per_million=(
            Decimal(cached_rate) if cached_rate is not None else None
        ),
    )


class CachedTokenPricingTests(SimpleTestCase):
    def test_cached_tokens_bill_at_the_cached_rate(self):
        cost = BillingService()._calculate_cost(
            _llm("2.00", "8.00", "0.50"), 1_000_000, 0, cached_input_tokens=500_000
        )
        self.assertEqual(cost, Decimal("1.25"))

    def test_without_a_cached_rate_cached_tokens_bill_as_input(self):
        cost = BillingService()._calculate_cost(
            _llm("2.00", "8.00", None), 1_000_000, 0, cached_input_tokens=500_000
        )
        self.assertEqual(cost, Decimal("2.00"))


class CachedTokenExtractionTests(SimpleTestCase):
    def test_openai_reports_cached_prompt_tokens(self):
        chunk = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1934,
                completion_tokens=5,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1792),
            )
        )
        usage = OpenAIUsageExtractor.extract_from_chat_completion(chunk)
        self.assertEqual(usage["input_tokens"], 1934)
        self.assertEqual(usage["cached_input_tokens"], 1792)

    def test_claude_folds_cache_reads_and_writes_into_input(self):
        extractor = ClaudeUsageExtractor()
        extractor.extract_from_message_start(
            SimpleNamespace(
                message=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=9,
                        cache_read_input_tokens=7201,
                        cache_creation_input_tokens=0,
                    )
                )
            )
        )
        usage = extractor.extract_from_message_delta(
            SimpleNamespace(usage=SimpleNamespace(output_tokens=3))
        )
        self.assertEqual(usage["input_tokens"], 7210)
        self.assertEqual(usage["cached_input_tokens"], 7201)
        self.assertNotIn("cache_write_input_tokens", usage)

    def test_stopped_turn_keeps_provider_counts_and_estimates_the_rest(self):
        provisional = {
            "input_tokens": 7210,
            "output_tokens": 0,
            "cached_input_tokens": 7201,
            "provisional": True,
        }
        usage = estimate_usage(
            [{"role": "user", "content": "hi"}], None, "one two three", provisional
        )
        self.assertEqual(usage["input_tokens"], 7210)
        self.assertEqual(usage["cached_input_tokens"], 7201)
        self.assertGreater(usage["output_tokens"], 0)
        self.assertTrue(usage["estimated"])
        self.assertEqual(usage["estimated_fields"], ["output_tokens"])
        self.assertEqual(usage["stop_reason"], "stopped by user")
        self.assertNotIn("provisional", usage)

    def test_stopped_turn_without_provider_counts_estimates_both_sides(self):
        usage = estimate_usage([{"role": "user", "content": "hi"}], None, "")
        self.assertGreater(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["estimated_fields"], ["input_tokens", "output_tokens"])

    def test_accumulator_sums_cached_tokens_across_rounds(self):
        usage = UsageAccumulator()
        usage.observe(1, {"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 60})
        usage.observe(2, {"input_tokens": 200, "output_tokens": 5, "cached_input_tokens": 150})
        self.assertEqual(usage.totals()["cached_input_tokens"], 210)
        self.assertEqual(usage.breakdown()[1]["cached_input_tokens"], 150)
