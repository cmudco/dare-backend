import importlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from config import env
from core.helpers.embeddings import EmbeddingClient
from memory.services.embeddings import embed_texts

EMBEDDING_VARS = [
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_SEND_DIMENSIONS",
    "MEMORY_EMBEDDING_BASE_URL",
    "MEMORY_EMBEDDING_API_KEY",
    "MEMORY_EMBEDDING_MODEL",
    "MEMORY_EMBEDDING_DIMENSIONS",
    "MEMORY_EMBEDDING_SEND_DIMENSIONS",
]


def reload_env(**overrides):
    environ = {k: v for k, v in os.environ.items() if k not in EMBEDDING_VARS}
    environ.update(overrides)
    with patch.dict(os.environ, environ, clear=True), patch("environ.Env.read_env"):
        return importlib.reload(env)


class EmbeddingSettingsTests(SimpleTestCase):
    def tearDown(self):
        importlib.reload(env)

    def test_defaults_match_the_openai_behaviour(self):
        settings = reload_env(OPENAI_API_KEY="sk-openai")

        self.assertIsNone(settings.EMBEDDING_BASE_URL)
        self.assertEqual(settings.EMBEDDING_API_KEY, "sk-openai")
        self.assertEqual(settings.EMBEDDING_MODEL, "text-embedding-3-large")
        self.assertEqual(settings.EMBEDDING_DIMENSIONS, 3072)
        self.assertFalse(settings.EMBEDDING_SEND_DIMENSIONS)
        self.assertIsNone(settings.MEMORY_EMBEDDING_BASE_URL)
        self.assertEqual(settings.MEMORY_EMBEDDING_API_KEY, "sk-openai")
        self.assertEqual(settings.MEMORY_EMBEDDING_MODEL, "text-embedding-3-small")
        self.assertEqual(settings.MEMORY_EMBEDDING_DIMENSIONS, 512)
        self.assertTrue(settings.MEMORY_EMBEDDING_SEND_DIMENSIONS)

    def test_memory_falls_back_to_the_document_endpoint(self):
        settings = reload_env(
            OPENAI_API_KEY="sk-openai",
            EMBEDDING_BASE_URL="http://embed.local/v1",
            EMBEDDING_API_KEY="local-key",
        )

        self.assertEqual(settings.MEMORY_EMBEDDING_BASE_URL, "http://embed.local/v1")
        self.assertEqual(settings.MEMORY_EMBEDDING_API_KEY, "local-key")


class EmbeddingClientTests(SimpleTestCase):
    def response(self, *vectors):
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])

    @patch("core.helpers.embeddings.OpenAI")
    def test_uses_configured_endpoint_key_and_model_without_dimensions(self, openai):
        openai.return_value.embeddings.create.return_value = self.response([0.1, 0.2])
        client = EmbeddingClient(
            base_url="http://localhost:11434/v1",
            api_key="local",
            model="nomic-embed-text",
            dimensions=768,
            send_dimensions=False,
        )

        self.assertEqual(client.create_batch_embeddings(["a"]), [[0.1, 0.2]])
        openai.assert_called_once_with(
            api_key="local", base_url="http://localhost:11434/v1"
        )
        openai.return_value.embeddings.create.assert_called_once_with(
            model="nomic-embed-text", input=["a"]
        )

    @patch("core.helpers.embeddings.OpenAI")
    def test_sends_dimensions_only_when_asked(self, openai):
        openai.return_value.embeddings.create.return_value = self.response([0.1])
        client = EmbeddingClient(model="m", dimensions=1024, send_dimensions=True)

        client.create_embeddings("a")

        openai.return_value.embeddings.create.assert_called_once_with(
            model="m", input="a", dimensions=1024
        )


class MemoryEmbeddingLengthTests(SimpleTestCase):
    @patch("memory.services.embeddings._get_client")
    def test_drops_vectors_that_do_not_fit_the_column(self, get_client):
        get_client.return_value = MagicMock(
            create_batch_embeddings=MagicMock(return_value=[[0.0] * 512, [0.0] * 768])
        )

        results = embed_texts(["fits", "too long"])

        self.assertEqual(len(results[0]), 512)
        self.assertIsNone(results[1])
