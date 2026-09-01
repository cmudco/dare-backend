from django.test import SimpleTestCase

from billing.litellm_model_policy import recommend_background_model


class BackgroundModelRecommendationTests(SimpleTestCase):
    def test_prefers_luna_with_proxy_prefixes(self):
        models = [
            "gemini/gemini-3.7-flash",
            "bedrock_mantle/openai.gpt-5.6-luna",
        ]

        self.assertEqual(
            recommend_background_model(models),
            "bedrock_mantle/openai.gpt-5.6-luna",
        )

    def test_prefers_newest_stable_gemini_flash(self):
        models = [
            "gemini/gemini-3.1-flash-lite",
            "gemini/gemini-3.7-flash-preview",
            "gemini/gemini-3.7-flash",
            "gemini/gemini-3.6-flash",
        ]

        self.assertEqual(recommend_background_model(models), "gemini/gemini-3.7-flash")

    def test_uses_lightweight_text_model_as_third_layer(self):
        models = ["text-embedding-3-large", "anthropic/claude-haiku-4-5"]

        self.assertEqual(
            recommend_background_model(models), "anthropic/claude-haiku-4-5"
        )

    def test_uses_first_generic_text_model_as_last_resort(self):
        models = ["image/generator", "custom/chat-model", "custom/other-model"]

        self.assertEqual(recommend_background_model(models), "custom/chat-model")

    def test_returns_none_when_no_text_model_exists(self):
        self.assertIsNone(
            recommend_background_model(["text-embedding-3-large", "voice/tts"])
        )
