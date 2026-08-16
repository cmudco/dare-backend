from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services.llm_helpers.retrieval_targets import (
    ChatRetrievalTarget, WorkflowRetrievalTarget)


def _chunk(**overrides):
    defaults = {
        "file_id": "4",
        "text": "passage",
        "score": 0.4,
        "rerank_score": 0.9,
        "chunk_index": 2,
        "library": None,
        "source_ref": "Fields Paul p.78",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class ChatRetrievalTargetTests(SimpleTestCase):
    def test_delegates_to_message_snippet_helpers(self):
        message = SimpleNamespace(retrieval_trace={"old": True})
        target = ChatRetrievalTarget(message)
        chunk = _chunk()

        with patch(
            "core.services.llm_helpers.retrieval_targets.save_document_snippet"
        ) as save_doc:
            target.save_document_snippet(chunk)
        save_doc.assert_called_once_with(message, chunk)

        with patch(
            "core.services.llm_helpers.retrieval_targets.save_retrieval_trace"
        ) as save_trace:
            target.save_trace({"stages": []})
        save_trace.assert_called_once_with(message, {"stages": []})

    def test_begin_agentic_search_resets_trace_exactly_once(self):
        message = SimpleNamespace(retrieval_trace={"old": True})
        target = ChatRetrievalTarget(message)

        target.begin_agentic_search()
        self.assertIsNone(message.retrieval_trace)

        message.retrieval_trace = {"from_first_search": True}
        target.begin_agentic_search()
        self.assertEqual(message.retrieval_trace, {"from_first_search": True})


class WorkflowRetrievalTargetTests(SimpleTestCase):
    def test_document_snippet_resolves_file_and_prefers_rerank_score(self):
        run_step = SimpleNamespace(retrieval_trace=None)
        target = WorkflowRetrievalTarget(run_step)
        file_row = MagicMock()

        with (
            patch("core.services.llm_helpers.retrieval_targets.File") as file_model,
            patch(
                "core.services.llm_helpers.retrieval_targets.WorkflowStepSnippet"
            ) as snippet_model,
        ):
            file_model.active_objects.get.return_value = file_row
            target.save_document_snippet(_chunk())

        file_model.active_objects.get.assert_called_once_with(id=4)
        kwargs = snippet_model.active_objects.create.call_args.kwargs
        self.assertIs(kwargs["workflow_run_step"], run_step)
        self.assertIs(kwargs["file"], file_row)
        self.assertIsNone(kwargs["library"])
        self.assertEqual(kwargs["similarity_score"], 0.9)
        self.assertEqual(kwargs["chunk_index"], 2)

    def test_library_snippet_persists_without_file(self):
        run_step = SimpleNamespace(retrieval_trace=None)
        target = WorkflowRetrievalTarget(run_step)
        library = MagicMock()

        with patch(
            "core.services.llm_helpers.retrieval_targets.WorkflowStepSnippet"
        ) as snippet_model:
            target.save_library_snippet(_chunk(library=library, rerank_score=None))

        kwargs = snippet_model.active_objects.create.call_args.kwargs
        self.assertIsNone(kwargs["file"])
        self.assertIs(kwargs["library"], library)
        self.assertEqual(kwargs["similarity_score"], 0.4)

    def test_save_trace_appends_second_source_on_the_run_step(self):
        saved_fields = []

        run_step = SimpleNamespace(retrieval_trace=None)
        run_step.save = lambda update_fields: saved_fields.append(update_fields)
        target = WorkflowRetrievalTarget(run_step)

        target.save_trace({"source": "documents"})
        self.assertEqual(run_step.retrieval_trace, {"source": "documents"})

        target.save_trace({"source": "libraries"})
        self.assertEqual(
            run_step.retrieval_trace,
            {"traces": [{"source": "documents"}, {"source": "libraries"}]},
        )
        self.assertEqual(saved_fields, [["retrieval_trace"], ["retrieval_trace"]])

    def test_snippet_failures_never_raise(self):
        run_step = SimpleNamespace(retrieval_trace=None)
        target = WorkflowRetrievalTarget(run_step)

        with patch(
            "core.services.llm_helpers.retrieval_targets.WorkflowStepSnippet"
        ) as snippet_model:
            snippet_model.active_objects.create.side_effect = RuntimeError("db down")
            target.save_library_snippet(_chunk(library=MagicMock()))
