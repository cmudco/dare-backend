from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from workflows.handlers.event_emitter import EventEmitter
from workflows.services.tool_loop_binding import (WorkflowStreamSink,
                                                  WorkflowToolLoopBinding)


def _run_step():
    return SimpleNamespace(
        id=11, workflow_run_id=5, retrieval_trace=None, context_trace=None
    )


class WorkflowToolLoopBindingTests(SimpleTestCase):
    def setUp(self):
        self.sent = []

        async def send(payload):
            self.sent.append(payload)

        self.send = send
        self.run_step = _run_step()
        self.binding = WorkflowToolLoopBinding(
            run_step=self.run_step,
            node_id="node-1",
            user=SimpleNamespace(id=3),
            emitter=EventEmitter(send, workflow_run_id=5),
            send_callback=send,
        )

    def test_correlation_and_turn_key_identify_the_step(self):
        self.assertEqual(
            self.binding.correlation,
            {"workflow_run_id": 5, "node_id": "node-1", "run_step_id": 11},
        )
        self.assertEqual(self.binding.store.turn_key, "ws11")
        self.assertIsNone(self.binding.message)
        self.assertIsNone(self.binding.conversation)
        self.assertIs(self.binding.store.retrieval_target.run_step, self.run_step)

    def test_save_tool_call_persists_the_step_row(self):
        with patch(
            "workflows.services.tool_loop_binding.WorkflowStepToolCall"
        ) as tool_call_model:
            async_to_sync(self.binding.store.save_tool_call)(
                call=SimpleNamespace(id="call-1", name="search_documents"),
                server_slug="dare",
                origin="dare",
                arguments={"query": "pensions"},
                raw_result={"success": True},
                is_error=False,
                error="",
                round_index=2,
                execution_time_ms=17,
            )

        kwargs = tool_call_model.objects.create.call_args.kwargs
        self.assertIs(kwargs["workflow_run_step"], self.run_step)
        self.assertEqual(kwargs["tool_call_id"], "call-1")
        self.assertEqual(kwargs["status"], "completed")
        self.assertEqual(kwargs["round_index"], 2)
        self.assertEqual(kwargs["execution_time_ms"], 17)

    def test_billing_gate_always_passes(self):
        can_continue, error = async_to_sync(self.binding.gate.check)(
            {"input_tokens": 999999}
        )
        self.assertTrue(can_continue)
        self.assertIsNone(error)

    def test_sink_derives_deltas_from_accumulated_text(self):
        sink = WorkflowStreamSink(EventEmitter(self.send, workflow_run_id=5), "node-1")

        async_to_sync(sink.text)("Hel")
        async_to_sync(sink.text)("Hello wo")
        async_to_sync(sink.text)("Hello wo")  # no growth: no event
        async_to_sync(sink.text)("Hello world")

        chunks = [p["chunk"] for p in self.sent]
        self.assertEqual(chunks, ["Hel", "lo wo", "rld"])
        self.assertEqual([p["accumulatedTokens"] for p in self.sent], [1, 2, 3])

    def test_save_context_trace_updates_only_the_trace_column(self):
        saved = []
        self.run_step.save = lambda update_fields: saved.append(update_fields)

        async_to_sync(self.binding.store.save_context_trace)({"stages": [{"n": 1}]})

        self.assertEqual(self.run_step.context_trace, {"stages": [{"n": 1}]})
        self.assertEqual(saved, [["context_trace"]])
