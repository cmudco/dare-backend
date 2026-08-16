from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from conversations.services.tool_execution_service import (
    ToolExecutionContext, ToolExecutionService)
from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import LLMStreamEvent, ToolCallRequest, ToolCallResult
from workflows.handlers.event_emitter import EventEmitter
from workflows.services.tool_loop_binding import WorkflowToolLoopBinding


def _run_step(step_id=11, run_id=5):
    return SimpleNamespace(
        id=step_id,
        workflow_run_id=run_id,
        retrieval_trace=None,
        context_trace=None,
    )


def _binding(run_step, sent):
    async def send(payload):
        sent.append(payload)

    return WorkflowToolLoopBinding(
        run_step=run_step,
        node_id="node-1",
        user=SimpleNamespace(id=3),
        emitter=EventEmitter(send, workflow_run_id=run_step.workflow_run_id),
        send_callback=send,
    )


class _TextOnlyLLMService:
    async def prepare_chat(self, request):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "summarize"}],
            tools=None,
            memory_context=[],
            context_trace=None,
        )

    async def stream_round(self, prepared, messages, tools):
        for text in ("Hello", " world"):
            yield LLMStreamEvent.text_delta(text)
        yield LLMStreamEvent.usage_frame({"input_tokens": 5, "output_tokens": 2})


class _AgenticLLMService:
    def __init__(self):
        self.rounds = 0

    async def prepare_chat(self, request):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "compare the documents"}],
            tools=[{"name": "search_documents"}],
            memory_context=[],
            context_trace=None,
        )

    async def stream_round(self, prepared, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            call = ToolCallRequest(
                id="search-1",
                name="search_documents",
                arguments='{"query":"conclusions"}',
            )
            yield LLMStreamEvent.tool_call_start("search-1", "search_documents")
            yield LLMStreamEvent.tool_call_ready(call)
            return
        yield LLMStreamEvent.text_delta("Both documents agree.")


class _RecordingExecutionService:
    def __init__(self):
        self.calls = []

    async def execute_round(self, tool_calls, ctx, round_index):
        results = []
        for call in tool_calls:
            self.calls.append((round_index, call.name, ctx.store.turn_key))
            results.append(
                ToolCallResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    origin="dare",
                    server_slug="dare",
                    content="3 passages found",
                    raw_result={"success": True, "passages_found": 3},
                )
            )
        return results


class WorkflowStepToolLoopTests(SimpleTestCase):
    async def test_text_only_step_streams_deltas_and_returns_full_text(self):
        sent = []
        service = ToolLoopService(_TextOnlyLLMService())

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(_run_step(), sent),
            retrieval_scope=None,
        )

        self.assertEqual(result.text, "Hello world")
        self.assertEqual(result.token_usage["input_tokens"], 5)
        streaming = [p for p in sent if p.get("type") == "step_streaming"]
        self.assertEqual([p["chunk"] for p in streaming], ["Hello", " world"])
        self.assertTrue(all(p["nodeId"] == "node-1" for p in streaming))

    async def test_agentic_step_executes_search_documents_with_workflow_events(self):
        sent = []
        run_step = _run_step()
        service = ToolLoopService(_AgenticLLMService())
        execution_service = _RecordingExecutionService()
        service.execution_service = execution_service

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(run_step, sent),
            retrieval_scope=None,
        )

        self.assertEqual(result.text, "Both documents agree.")
        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(execution_service.calls, [(1, "search_documents", "ws11")])

        pending = [p for p in sent if p.get("type") == "tool_call_pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["workflowRunId"], 5)
        self.assertEqual(pending[0]["nodeId"], "node-1")
        self.assertEqual(pending[0]["runStepId"], 11)
        self.assertNotIn("messageId", pending[0])


class WorkflowMcpExecutionTests(SimpleTestCase):
    async def test_mcp_tool_runs_without_chat_context_and_skips_artifact_bridge(self):
        run_step = _run_step()
        sent = []
        binding = _binding(run_step, sent)
        ctx = ToolExecutionContext(
            message=None,
            conversation=None,
            user=SimpleNamespace(id=3),
            send_callback=binding.send_callback,
            emitter=None,
            store=binding.store,
        )

        mcp_result = {"content": [{"type": "text", "text": "Issue #12 summary"}]}
        with (
            patch(
                "conversations.services.tool_execution_service.mcp_tool_executor"
            ) as executor,
            patch(
                "conversations.services.tool_execution_service.maybe_create_pdf_artifact"
            ) as bridge,
        ):
            executor.execute_tool_call = AsyncMock(return_value=mcp_result)
            raw, content, is_error = await ToolExecutionService()._execute_mcp(
                "github", "get_issue", {"number": 12}, ctx
            )

        self.assertFalse(is_error)
        self.assertEqual(content, "Issue #12 summary")
        bridge.assert_not_called()
        executor.execute_tool_call.assert_awaited_once()
        self.assertIsNone(executor.execute_tool_call.await_args.kwargs["message"])
