"""Fresh Socratic conversations must not depend on a previous failed turn."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from core.services.document_processor import DocumentProcessor
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.llm_helpers.socratic_helpers import (
    build_advanced_socratic_messages,
    build_classic_socratic_messages,
)
from users.constants import AuthSourceChoice

HELPERS = "core.services.llm_helpers.semantic_context_helpers"


def fresh_processor():
    return DocumentProcessor(
        openai_client=Mock(),
        embedding_service=Mock(),
        parsing_service=Mock(),
        enrichment_service=Mock(),
        file_processor=Mock(),
    )


class ChatSwitchRetrievalTests(SimpleTestCase):
    def _check_fresh_conversations(self, search, expected_text, rag_mode="advanced"):
        # Each subscription creates a coordinator with a fresh processor.
        # Both turns must work even when an unused legacy client cannot open.
        with (
            patch(
                f"{HELPERS}.get_vector_service_async",
                new_callable=AsyncMock,
                side_effect=ConnectionError("Legacy vector client unavailable"),
                create=True,
            ) as legacy_init,
            patch(
                (
                    f"{HELPERS}.run_document_search"
                    if rag_mode == "advanced"
                    else "core.services.document_processor.get_vector_service"
                ),
                **search,
            ) as retrieval,
        ):
            for advanced, build in (
                (False, build_classic_socratic_messages),
                (True, build_advanced_socratic_messages),
            ):
                for subscription in range(2):
                    processor = fresh_processor()
                    for turn, message in enumerate(("hi", "ok"), start=1):
                        with self.subTest(
                            advanced=advanced, subscription=subscription, turn=turn
                        ):
                            request = LLMQueryRequestBuilder.from_message_data(
                                message=message,
                                user=SimpleNamespace(id=7),
                                platform=AuthSourceChoice.SOCRATIC_BOTS,
                                llm=SimpleNamespace(provider="gemini"),
                                message_data={
                                    "embedding_ids": [2452],
                                    "file_owner_id": 27,
                                    "rag_mode": rag_mode,
                                    "is_advanced": advanced,
                                    "bot_meta": {
                                        "subject": "Jenkins",
                                        "topic": "Pipelines",
                                    },
                                },
                            )
                            result = async_to_sync(build)(request, processor)
                            prompt = "\n".join(
                                item["content"] for item in result.messages
                            )
                            self.assertIn(expected_text, prompt)
                            self.assertTrue(result.context_trace["stages"])
            expected_calls = (
                4 if rag_mode == "naive" and "return_value" in search else 8
            )
            self.assertEqual(retrieval.call_count, expected_calls)
            legacy_init.assert_not_called()

    def test_first_and_later_turns_keep_document_evidence_after_switch(self):
        self._check_fresh_conversations(
            {"return_value": ["[S1] A Jenkins pipeline defines build stages."]},
            "[S1] A Jenkins pipeline defines build stages.",
        )

    def test_retrieval_failure_is_disclosed_without_breaking_first_turn(self):
        with self.assertLogs(HELPERS, level="ERROR") as captured:
            self._check_fresh_conversations(
                {"side_effect": ConnectionError("Document search unavailable")},
                "Some document retrieval failed. Do not claim the unavailable sources were searched.",
            )
        self.assertEqual(len(captured.records), 8)
        for record in captured.records:
            self.assertIsInstance(record.exc_info[1], ConnectionError)

    def test_naive_retrieval_initializes_its_client_and_keeps_evidence(self):
        service = Mock()
        service.search_documents.return_value = [
            {
                "score": 0.9,
                "metadata": {
                    "text": "A Jenkins pipeline defines build stages.",
                    "file_name": "Jenkins",
                },
            }
        ]
        self._check_fresh_conversations(
            {"return_value": service},
            "[S1] Jenkins:\nA Jenkins pipeline defines build stages.",
            rag_mode="naive",
        )
        self.assertEqual(service.search_documents.call_count, 8)

    def test_naive_initialization_failure_is_disclosed_and_retried(self):
        self._check_fresh_conversations(
            {"side_effect": ConnectionError("Document client unavailable")},
            "Some document retrieval failed. Do not claim the unavailable sources were searched.",
            rag_mode="naive",
        )
