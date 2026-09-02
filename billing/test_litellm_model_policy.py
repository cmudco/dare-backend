from django.test import SimpleTestCase

from billing.litellm_model_policy import recommend_background_models


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
