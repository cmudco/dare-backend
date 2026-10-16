from django.test import SimpleTestCase

from billing.litellm_model_policy import (
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


# The CMU gateway roster as Ask DARE saw it when it picked gpt-5.5.
GATEWAY_ROSTER = [
    "us.anthropic.claude-opus-5-5",
    "bedrock_mantle/openai.gpt-5.5",
    "bedrock_mantle/openai.gpt-5.6-sol",
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.1-pro-preview",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-2.5-flash-image",
    "gpt-5.4-pro",
    "gpt-5.5",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6.1-sol",
    "gpt-6-sol",
    "gpt-image-2",
    "text-embedding-3-small",
    "gpt-4o-transcribe",
]


class AgentModelRecommendationTests(SimpleTestCase):
    def test_recommends_the_newest_flagship_gpt_then_the_newest_gemini(self):
        self.assertEqual(
            recommend_agent_models(GATEWAY_ROSTER),
            ["gpt-6.1-sol", "gemini/gemini-3.8-flash"],
        )

    def test_prefers_the_clean_id_over_a_provider_alias(self):
        self.assertEqual(
            recommend_agent_models(
                ["bedrock_mantle/openai.gpt-5.6-sol", "gpt-5.6-sol"]
            ),
            ["gpt-5.6-sol"],
        )

    def test_non_chat_models_are_not_text_models(self):
        for model in ("text-embedding-3-small", "gpt-4o-transcribe", "gpt-image-2"):
            with self.subTest(model=model):
                self.assertFalse(is_text_model(model))
        self.assertTrue(is_text_model("gpt-6.1-sol"))
