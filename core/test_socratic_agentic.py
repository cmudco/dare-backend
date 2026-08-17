"""Socratic Bots agentic-RAG enablement and defensive gating."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from conversations.constants import RagMode
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.dtos.context_dto import ContextConfig
from core.services.dtos.generation_dto import GenerationConfig
from core.services.dtos.media_dto import MediaConfig
from core.services.dtos.request_dto import LLMQueryRequest
from core.services.dtos.socratic_dto import SocraticConfig
from core.services.llm_helpers.socratic_helpers import (
    AGENTIC_RETRIEVAL_DIRECTIVE, build_advanced_socratic_messages,
    build_classic_socratic_messages)
from users.constants import AuthSourceChoice


def _llm(provider="openai"):
    return SimpleNamespace(provider=provider)


class SocraticBuilderGatingTests(SimpleTestCase):
    def _build(self, *, user=SimpleNamespace(id=1), **payload):
        message_data = {
            "embedding_ids": [4, 5],
            "is_advanced": True,
            **payload,
        }
        return LLMQueryRequestBuilder.from_message_data(
            message="what does the reading say?",
            user=user,
            message_data=message_data,
            llm=_llm(),
            platform=AuthSourceChoice.SOCRATIC_BOTS,
        )

    def test_agentic_socratic_exposes_search_documents(self):
        request = self._build(rag_mode=RagMode.AGENTIC)
        self.assertEqual(request.context.rag_mode, RagMode.AGENTIC)
        self.assertEqual(request.dare_tool_slugs, ("search_documents",))
        self.assertTrue(request.socratic.enabled)

    def test_default_rag_mode_stays_advanced_with_no_tools(self):
        request = self._build()
        self.assertEqual(request.context.rag_mode, RagMode.ADVANCED)
        self.assertEqual(request.dare_tool_slugs, ())

    def test_anonymous_session_downgrades_agentic_to_advanced(self):
        request = self._build(user=None, rag_mode=RagMode.AGENTIC)
        self.assertEqual(request.context.rag_mode, RagMode.ADVANCED)
        self.assertEqual(request.dare_tool_slugs, ())

    def test_artifacts_and_mcp_are_stripped_for_socratic(self):
        request = self._build(
            rag_mode=RagMode.AGENTIC,
            artifacts_enabled=True,
            mcp_server_ids=[3, 9],
            dare_tool_slugs=["create_chart", "search_documents", "generate_image"],
        )
        self.assertFalse(request.generation.artifacts_enabled)
        self.assertEqual(request.mcp_server_ids, ())
        self.assertEqual(request.dare_tool_slugs, ("search_documents",))

    def test_non_socratic_chat_keeps_artifacts_and_mcp(self):
        request = LLMQueryRequestBuilder.from_message_data(
            message="make a chart",
            user=SimpleNamespace(id=1),
            message_data={
                "artifacts_enabled": True,
                "mcp_server_ids": [3],
            },
            llm=_llm(),
            platform=None,
        )
        self.assertTrue(request.generation.artifacts_enabled)
        self.assertEqual(request.mcp_server_ids, (3,))


def _socratic_request(rag_mode):
    return LLMQueryRequest(
        message="what is chapter two about?",
        conversation=None,
        user=SimpleNamespace(id=1),
        llm=_llm(),
        context=ContextConfig(embedding_ids=[4], rag_mode=rag_mode),
        generation=GenerationConfig(),
        media=MediaConfig(),
        socratic=SocraticConfig(
            enabled=True,
            advanced_mode=False,
            bot_meta={
                "subject": "history",
                "topic": "ww2",
                "learning_goals": "causes",
                "chat_prompt": "be socratic",
            },
        ),
    )


class SocraticBuilderAgenticSkipTests(SimpleTestCase):
    def setUp(self):
        self.processor = SimpleNamespace(
            user_id=1,
            vector_service=None,
            search_similar_documents=AsyncMock(return_value="snippet text"),
        )

    def test_classic_agentic_skips_pre_injection(self):
        messages = async_to_sync(build_classic_socratic_messages)(
            _socratic_request(RagMode.AGENTIC), self.processor
        )
        self.processor.search_similar_documents.assert_not_called()
        self.assertIn(AGENTIC_RETRIEVAL_DIRECTIVE, messages[1]["content"])

    def test_classic_non_agentic_still_pre_injects(self):
        messages = async_to_sync(build_classic_socratic_messages)(
            _socratic_request(RagMode.ADVANCED), self.processor
        )
        self.processor.search_similar_documents.assert_called_once()
        self.assertIn("snippet text", messages[1]["content"])

    def test_advanced_agentic_skips_pre_injection(self):
        messages = async_to_sync(build_advanced_socratic_messages)(
            _socratic_request(RagMode.AGENTIC), self.processor
        )
        self.processor.search_similar_documents.assert_not_called()
        self.assertIn(AGENTIC_RETRIEVAL_DIRECTIVE, messages[0]["content"])

    def test_advanced_non_agentic_still_pre_injects(self):
        messages = async_to_sync(build_advanced_socratic_messages)(
            _socratic_request(RagMode.ADVANCED), self.processor
        )
        self.processor.search_similar_documents.assert_called_once()
        self.assertIn("snippet text", messages[0]["content"])
