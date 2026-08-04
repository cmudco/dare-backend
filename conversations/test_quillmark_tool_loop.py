from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import LLMStreamEvent, ToolCallRequest, ToolCallResult


class _QuillmarkChainLLMService:
    def __init__(self):
        self.round_messages = []

    async def prepare_chat(self, request):
        return SimpleNamespace(
            messages=[
                {
                    "role": "user",
                    "content": "Create a CMU memo to Dean Rivera about FY27 funding.",
                }
            ],
            tools=[
                {"name": "quillmark__get_spec"},
                {"name": "quillmark__create_document"},
            ],
            memory_context=[],
            context_trace=None,
        )

    async def stream_round(self, prepared, messages, tools):
        self.round_messages.append([dict(message) for message in messages])
        round_index = len(self.round_messages)
        if round_index == 1:
            call = ToolCallRequest(
                id="spec-1",
                name="quillmark__get_spec",
                arguments='{"quill":"cmu_memo@0.1.0"}',
            )
            yield LLMStreamEvent.tool_call_start(call.id, call.name)
            yield LLMStreamEvent.tool_call_ready(call)
        elif round_index == 2:
            call = ToolCallRequest(
                id="render-1",
                name="quillmark__create_document",
                arguments='{"content":"~~~card-yaml\\n$quill: cmu_memo@0.1.0"}',
            )
            yield LLMStreamEvent.tool_call_start(call.id, call.name)
            yield LLMStreamEvent.tool_call_ready(call)
        else:
            yield LLMStreamEvent.text_delta("Your CMU memo is ready.")


class _QuillmarkChainExecutionService:
    def __init__(self):
        self.calls = []

    async def execute_round(self, calls, ctx, round_index):
        self.calls.extend((round_index, call.name) for call in calls)
        results = []
        for call in calls:
            content = (
                "Required fields: to, from, date, subject."
                if call.name.endswith("get_spec")
                else "PDF artifact created."
            )
            results.append(
                ToolCallResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    origin="mcp",
                    server_slug="quillmark",
                    content=content,
                    raw_result={"content": [{"type": "text", "text": content}]},
                )
            )
        return results


class QuillmarkToolLoopTests(SimpleTestCase):
    async def test_get_spec_then_create_document_keeps_original_request(self):
        llm_service = _QuillmarkChainLLMService()
        execution_service = _QuillmarkChainExecutionService()
        service = ToolLoopService(llm_service, billing_service=None)
        service.execution_service = execution_service
        sent = []

        async def send(payload):
            sent.append(payload)

        result = await service.run(
            request=SimpleNamespace(),
            message_obj=SimpleNamespace(id=296, created_at=timezone.now()),
            llm=SimpleNamespace(),
            user=None,
            conversation=SimpleNamespace(),
            send_callback=send,
            retrieval_scope=None,
        )

        self.assertEqual(
            execution_service.calls,
            [
                (1, "quillmark__get_spec"),
                (2, "quillmark__create_document"),
            ],
        )
        self.assertEqual(result.text, "Your CMU memo is ready.")
        self.assertEqual(result.tool_calls_made, 2)
        self.assertEqual(result.rounds_used, 3)

        for round_messages in llm_service.round_messages:
            self.assertEqual(
                round_messages[0]["content"],
                "Create a CMU memo to Dean Rivera about FY27 funding.",
            )

        final_roles = [message["role"] for message in llm_service.round_messages[-1]]
        self.assertEqual(
            final_roles, ["user", "assistant", "tool", "assistant", "tool"]
        )
