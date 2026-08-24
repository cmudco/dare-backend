from types import SimpleNamespace

from django.test import SimpleTestCase

from core.services.model_capabilities import ModelCapabilities
from core.services.model_identity import normalize_identifier, resolve_family

# Identifiers observed on the LiteLLM proxy, paired with the identity each
# reduces to. Frozen so a normalizer change has to confront the real roster.
OBSERVED_ROSTER = (
    ("gpt-5.6-sol", "gpt-5.6"),
    ("wine-claude-opus-4-6", "claude-opus-4-6"),
    ("bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0", "anthropic-claude-opus-4-5"),
    ("bedrock/us.deepseek.r1-v1:0", "deepseek-r1"),
    ("vertex_ai/gemini-3.1-flash-lite", "gemini-3.1-flash-lite"),
    (
        "bedrock/us.meta.llama4-scout-17b-instruct-v1:0",
        "meta-llama4-scout-17b-instruct",
    ),
    ("bedrock/qwen.qwen3-32b-v1:0", "qwen-qwen3-32b"),
    ("wine-gemini-embedding-001", "gemini-embedding-001"),
)


class NormalizeIdentifierTests(SimpleTestCase):
    def test_reduces_deployment_addresses_to_model_identities(self):
        for raw, expected in OBSERVED_ROSTER:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_identifier(raw), expected)

    def test_vendor_namespace_is_kept_when_it_carries_the_model_name(self):
        # "deepseek.r1" would reduce to a meaningless "r1" if the namespace
        # were dropped the way "anthropic.claude-..." can be.
        self.assertEqual(
            normalize_identifier("bedrock/us.deepseek.r1-v1:0"), "deepseek-r1"
        )


class ResolveFamilyTests(SimpleTestCase):
    def test_reasoning_model_behind_a_proxy_resolves_despite_tier_suffix(self):
        family = resolve_family("gpt-5.6-sol")
        self.assertIsNotNone(family)
        self.assertTrue(family.is_reasoning)
        self.assertFalse(family.supports_temperature)
        self.assertTrue(family.supports_effort)

    def test_ordinary_chat_models_have_no_family(self):
        for identifier in (
            "gpt-4o",
            "wine-claude-haiku-4-5",
            "vertex_ai/gemini-2.5-pro",
        ):
            with self.subTest(identifier=identifier):
                self.assertIsNone(resolve_family(identifier))


class CapabilityResolutionTests(SimpleTestCase):
    def test_proxy_routed_reasoning_model_drops_temperature(self):
        # The reported failure: temperature=0.7 reached gpt-5.6 and 400'd.
        capabilities = ModelCapabilities.from_llm(
            SimpleNamespace(identifier="gpt-5.6-sol", provider="custom")
        )
        params = {}
        capabilities.apply_sampling_params(params, temperature=0.7)
        self.assertNotIn("temperature", params)

    def test_explicit_flag_outranks_the_family(self):
        capabilities = ModelCapabilities.from_llm(
            SimpleNamespace(
                identifier="gpt-5.6", provider="openai", supports_temperature=True
            )
        )
        self.assertTrue(capabilities.supports_temperature)

    def test_unfamilied_model_keeps_temperature(self):
        capabilities = ModelCapabilities.from_llm(
            SimpleNamespace(identifier="wine-llama3-70b-instruct", provider="custom")
        )
        params = {}
        capabilities.apply_sampling_params(params, temperature=0.7)
        self.assertEqual(params["temperature"], 0.7)
