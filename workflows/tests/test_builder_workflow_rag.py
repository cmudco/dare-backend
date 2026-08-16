from types import SimpleNamespace

from django.test import SimpleTestCase

from conversations.constants import RagMode
from core.services.dtos.builder import LLMQueryRequestBuilder


def _llm(provider="openai"):
    return SimpleNamespace(provider=provider)


class WorkflowRequestBuilderRagTests(SimpleTestCase):
    def _build(self, **overrides):
        kwargs = {
            "message": "compare the documents",
            "user": SimpleNamespace(id=1),
            "llm": _llm(),
            "embedding_ids": [4, 5],
            "rag_mode": RagMode.AGENTIC,
        }
        kwargs.update(overrides)
        return LLMQueryRequestBuilder.from_workflow_data(**kwargs)

    def test_default_rag_mode_stays_naive(self):
        request = self._build(rag_mode=RagMode.NAIVE)
        self.assertEqual(request.context.rag_mode, RagMode.NAIVE)
        self.assertEqual(request.dare_tool_slugs, ())

    def test_agentic_mode_exposes_search_documents_tool(self):
        request = self._build()
        self.assertEqual(request.context.rag_mode, RagMode.AGENTIC)
        self.assertEqual(request.dare_tool_slugs, ("search_documents",))

    def test_agentic_without_sources_falls_back_to_advanced(self):
        request = self._build(embedding_ids=None)
        self.assertEqual(request.context.rag_mode, RagMode.ADVANCED)
        self.assertEqual(request.dare_tool_slugs, ())

    def test_agentic_on_llama_falls_back_to_advanced(self):
        request = self._build(llm=_llm(provider="llama"))
        self.assertEqual(request.context.rag_mode, RagMode.ADVANCED)
        self.assertEqual(request.dare_tool_slugs, ())

    def test_agentic_with_only_libraries_keeps_the_tool(self):
        request = self._build(embedding_ids=None, library_ids=[7])
        self.assertEqual(request.context.library_ids, [7])
        self.assertEqual(request.dare_tool_slugs, ("search_documents",))

    def test_library_ids_reach_the_context(self):
        request = self._build(rag_mode=RagMode.ADVANCED, library_ids=[7, 9])
        self.assertEqual(request.context.library_ids, [7, 9])
