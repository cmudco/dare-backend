from decimal import Decimal

from django.test import SimpleTestCase

from billing.litellm_model_policy import (
    PricedModel,
    is_text_model,
    recommend_agent_models,
    recommend_background_models,
    recommend_vision_models,
)
from core.services.model_identity import supports_vision


class BackgroundModelRecommendationTests(SimpleTestCase):
    def test_recommends_one_canonical_luna_then_newest_gemini_flash(self):
        models = [
            "gemini/gemini-3.6-flash",
            "openai/gpt-5.6-sol",
            "gemini/gemini-3.7-flash",
            "bedrock_mantle/openai.gpt-5.6-luna",
            "gpt-5.6-luna",
            "bedrock_mantle/openai.gpt-5.6-terra",
            "gemini/gemini-3.5-flash",
            "gemini/gemini-3.5-pro",
        ]

        self.assertEqual(
            recommend_background_models(models),
            [
                "gpt-5.6-luna",
                "gemini/gemini-3.7-flash",
                "gemini/gemini-3.6-flash",
                "gemini/gemini-3.5-flash",
            ],
        )

    def test_caps_many_flash_models_in_descending_order(self):
        models = [
            "gemini/gemini-3.1-flash-lite",
            "gemini/gemini-3.7-flash-preview",
            "gemini/gemini-3.7-flash",
            "gemini/gemini-3.6-flash",
            "gemini/gemini-3.5-flash",
        ]

        self.assertEqual(
            recommend_background_models(models),
            [
                "gemini/gemini-3.7-flash",
                "gemini/gemini-3.7-flash-preview",
                "gemini/gemini-3.6-flash",
                "gemini/gemini-3.5-flash",
            ],
        )

    def test_uses_other_gemini_models_after_flash(self):
        models = [
            "gemini/gemini-2.5-pro",
            "gemini/gemini-3.1-pro",
            "gemini/gemini-3.0-flash",
        ]

        self.assertEqual(
            recommend_background_models(models),
            [
                "gemini/gemini-3.0-flash",
                "gemini/gemini-3.1-pro",
                "gemini/gemini-2.5-pro",
            ],
        )

    def test_uses_newest_haiku_after_gemini(self):
        models = [
            "anthropic/claude-3-haiku-20240307",
            "anthropic/claude-haiku-4-5",
            "gemini/gemini-3.0-flash",
            "anthropic/claude-3-5-haiku-20241022",
            "anthropic/claude-sonnet-4-5",
        ]

        self.assertEqual(
            recommend_background_models(models),
            [
                "gemini/gemini-3.0-flash",
                "anthropic/claude-haiku-4-5",
                "anthropic/claude-3-5-haiku-20241022",
                "anthropic/claude-3-haiku-20240307",
            ],
        )

    def test_excludes_other_families_non_text_models_and_duplicates(self):
        models = [
            "anthropic/claude-sonnet-4-5",
            "text-embedding-3-large",
            "openai/gpt-5.6-luna",
            "OPENAI/GPT-5.6-LUNA",
            "image/gemini-3.7-flash",
        ]

        self.assertEqual(
            recommend_background_models(models),
            ["openai/gpt-5.6-luna"],
        )

    def test_honors_custom_limit(self):
        models = ["gpt-5.6-luna", "gemini-3.7-flash", "gemini-3.6-flash"]

        self.assertEqual(recommend_background_models(models, limit=2), models[:2])
        self.assertEqual(recommend_background_models(models, limit=0), [])


class VisionModelPolicyTests(SimpleTestCase):
    def test_known_families_support_vision_and_special_purpose_models_do_not(self):
        for model in (
            "openai/gpt-5.6-luna",
            "gpt-4.1-mini",
            "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "anthropic/claude-fable-5-1",
            "gemini/gemini-3.6-flash",
            "mistral/pixtral-large-latest",
        ):
            self.assertTrue(supports_vision(model), model)
        for model in (
            "text-embedding-3-large",
            "gpt-4o-mini-tts",
            "gpt-4o-transcribe",
            "o3-mini",
            "deepseek/deepseek-r1",
            "gpt-image-1",
        ):
            self.assertFalse(supports_vision(model), model)

    def test_ranks_gemini_flash_then_luna_then_haiku_then_the_rest(self):
        models = [
            "anthropic/claude-sonnet-4-5",
            "text-embedding-3-large",
            "anthropic/claude-haiku-4-5",
            "gpt-5.6-luna",
            "gemini/gemini-3.5-flash",
            "gemini/gemini-3.6-flash",
            "deepseek/deepseek-r1",
        ]

        self.assertEqual(
            recommend_vision_models(models),
            [
                "gemini/gemini-3.6-flash",
                "gemini/gemini-3.5-flash",
                "gpt-5.6-luna",
                "anthropic/claude-haiku-4-5",
                "anthropic/claude-sonnet-4-5",
            ],
        )


def priced(prices):
    return [
        PricedModel(model, None if rate is None else Decimal(rate))
        for model, rate in prices.items()
    ]


CEILING = Decimal("10")

# The CMU gateway roster with output prices (USD per million tokens).
GATEWAY_PRICES = {
    "bedrock_mantle/openai.gpt-5.5": "30",
    "gemini/gemini-3.8-flash": "3.75",
    "gemini/gemini-3.1-pro-preview": "12",
    "gemini/gemini-3.5-flash-lite": "2.5",
    "gpt-5.4-pro": "180",
    "gpt-5.5": "30",
    "gpt-5.6-sol": "20",
    "gpt-6-astra": "50",
    "gpt-6-luna": "0.5",
    "gpt-6.1-sol": "10",
    "gpt-6-sol": "10",
    "text-embedding-3-small": "0",
}


class AgentModelRecommendationTests(SimpleTestCase):
    def recommend(self, prices):
        return recommend_agent_models(priced(prices), max_output_rate=CEILING)

    def test_recommends_the_newest_affordable_flagship_gpt_and_gemini(self):
        self.assertEqual(
            self.recommend(GATEWAY_PRICES),
            ["gpt-6.1-sol", "gemini/gemini-3.8-flash"],
        )

    def test_a_newer_model_above_the_ceiling_loses_to_an_affordable_one(self):
        prices = {**GATEWAY_PRICES, "gpt-7-sol": "60", "gemini/gemini-4.0-flash": "15"}

        self.assertEqual(
            self.recommend(prices), ["gpt-6.1-sol", "gemini/gemini-3.8-flash"]
        )

    def test_an_unpriced_model_is_never_recommended(self):
        prices = {**GATEWAY_PRICES, "gpt-7-sol": None}

        self.assertEqual(self.recommend(prices)[0], "gpt-6.1-sol")

    def test_flash_beats_pro_at_the_same_version(self):
        prices = {"gemini/gemini-3.9-pro": "8", "gemini/gemini-3.9-flash": "4"}

        self.assertEqual(self.recommend(prices), ["gemini/gemini-3.9-flash"])

    def test_nothing_affordable_means_no_recommendation(self):
        self.assertEqual(self.recommend({"gpt-6-astra": "50", "gpt-5.5": "30"}), [])

    def test_prefers_the_clean_id_over_a_provider_alias(self):
        prices = {"bedrock_mantle/openai.gpt-6-sol": "10", "gpt-6-sol": "10"}

        self.assertEqual(self.recommend(prices), ["gpt-6-sol"])

    def test_non_chat_models_are_not_text_models(self):
        for model in ("text-embedding-3-small", "gpt-4o-transcribe", "gpt-image-2"):
            with self.subTest(model=model):
                self.assertFalse(is_text_model(model))
        self.assertTrue(is_text_model("gpt-6.1-sol"))
