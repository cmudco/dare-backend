from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from conversations.services.tool_loop_binding import ChatToolLoopBinding
from conversations.services.tool_loop_service import ToolLoopService
from core.services.dtos import LLMStreamEvent, ToolCallRequest, ToolCallResult
from core.services.llm_utils.provider_message_converters import ClaudeMessageConverter


def _binding(message_obj, send_callback):
    return ChatToolLoopBinding(
        message_obj=message_obj,
        conversation=SimpleNamespace(),
        user=None,
        llm=SimpleNamespace(),
        send_callback=send_callback,
        billing_service=None,
    )


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


class _ClaudeInterleavedLLMService:
    def __init__(self):
        self.round_messages = []
        self.provider_content = [
            {
                "type": "thinking",
                "thinking": "Search the current evidence.",
                "signature": "signed-1",
            },
            {
                "type": "server_tool_use",
                "id": "srvtoolu-1",
                "name": "web_search",
                "input": {"query": "current evidence"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu-1",
                "content": [],
            },
            {
                "type": "redacted_thinking",
                "data": "opaque-redacted-data",
            },
            {"type": "text", "text": "First paragraph.\n\nSecond paragraph."},
            {
                "type": "tool_use",
                "id": "spec-1",
                "name": "quillmark__get_spec",
                "input": {"quill": "cmu_memo@0.1.0"},
            },
        ]

    async def prepare_chat(self, request):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "Research, then create a memo."}],
            tools=[{"name": "quillmark__get_spec"}],
            memory_context=[],
            context_trace=None,
        )

    async def stream_round(self, prepared, messages, tools):
        self.round_messages.append([dict(message) for message in messages])
        if len(self.round_messages) == 1:
            for index, block in enumerate(self.provider_content):
                yield LLMStreamEvent.provider_content_block_ready(block, index)
                if block["type"] == "text":
                    yield LLMStreamEvent.text_delta(block["text"])
            call = ToolCallRequest(
                id="spec-1",
                name="quillmark__get_spec",
                arguments='{"quill":"cmu_memo@0.1.0"}',
            )
            yield LLMStreamEvent.tool_call_start(call.id, call.name)
            yield LLMStreamEvent.tool_call_ready(call)
        else:
            yield LLMStreamEvent.text_delta("Final answer.")


class _ClaudeWebThenDareLLMService:
    def __init__(self):
        self.round_messages = []
        self.provider_content = [
            {
                "type": "thinking",
                "thinking": "Check current web evidence, then the user's sources.",
                "signature": "signed-web-dare",
            },
            {
                "type": "server_tool_use",
                "id": "srvtoolu-web",
                "name": "web_search",
                "input": {"query": "current conference accessibility guidance"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu-web",
                "content": [],
            },
            {
                "type": "tool_use",
                "id": "dare-search-1",
                "name": "search_documents",
                "input": {"query": "conference accessibility requirements"},
            },
        ]

    async def prepare_chat(self, request):
        return SimpleNamespace(
            messages=[
                {
                    "role": "user",
                    "content": "Compare current guidance with my documents.",
                }
            ],
            tools=[{"name": "search_documents"}],
            memory_context=[],
            context_trace=None,
        )

    async def stream_round(self, prepared, messages, tools):
        self.round_messages.append([dict(message) for message in messages])
        if len(self.round_messages) == 1:
            for index, block in enumerate(self.provider_content):
                yield LLMStreamEvent.provider_content_block_ready(block, index)
            call = ToolCallRequest(
                id="dare-search-1",
                name="search_documents",
                arguments='{"query":"conference accessibility requirements"}',
            )
            yield LLMStreamEvent.tool_call_start(call.id, call.name)
            yield LLMStreamEvent.tool_call_ready(call)
        else:
            yield LLMStreamEvent.text_delta(
                "The current guidance and your documents agree."
            )


class _DareSearchExecutionService:
    def __init__(self):
        self.calls = []

    async def execute_round(self, calls, ctx, round_index):
        self.calls.extend(
            (round_index, call.name, ctx.retrieval_scope) for call in calls
        )
        return [
            ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                origin="dare",
                server_slug=None,
                content="[S1] Provide accessible registration and materials.",
                raw_result={"success": True, "results": []},
            )
            for call in calls
        ]


class QuillmarkToolLoopTests(SimpleTestCase):
    async def test_get_spec_then_create_document_keeps_original_request(self):
        llm_service = _QuillmarkChainLLMService()
        execution_service = _QuillmarkChainExecutionService()
        service = ToolLoopService(llm_service)
        service.execution_service = execution_service
        sent = []

        async def send(payload):
            sent.append(payload)

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=296, created_at=timezone.now()), send),
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

    async def test_claude_tool_continuation_replays_full_provider_response(self):
        llm_service = _ClaudeInterleavedLLMService()
        service = ToolLoopService(llm_service)
        service.execution_service = _QuillmarkChainExecutionService()

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=297, created_at=timezone.now()), send),
            retrieval_scope=None,
        )

        continued_messages = llm_service.round_messages[1]
        assistant_turn = continued_messages[1]
        self.assertEqual(
            assistant_turn["content"], "First paragraph.\n\nSecond paragraph."
        )
        self.assertEqual(
            assistant_turn["provider_assistant_content"],
            llm_service.provider_content,
        )

        _, converted = ClaudeMessageConverter.convert(continued_messages)
        self.assertEqual(converted[1]["content"], llm_service.provider_content)
        self.assertEqual(
            result.text, "First paragraph.\n\nSecond paragraph.\n\nFinal answer."
        )

    async def test_claude_web_search_then_dare_tool_replays_native_blocks(self):
        llm_service = _ClaudeWebThenDareLLMService()
        execution_service = _DareSearchExecutionService()
        service = ToolLoopService(llm_service)
        service.execution_service = execution_service
        retrieval_scope = SimpleNamespace(file_ids=(11,), library_ids=(4,))

        async def send(_payload):
            return None

        result = await service.run(
            request=SimpleNamespace(),
            binding=_binding(SimpleNamespace(id=298, created_at=timezone.now()), send),
            retrieval_scope=retrieval_scope,
        )

        self.assertEqual(
            execution_service.calls,
            [(1, "search_documents", retrieval_scope)],
        )
        continued_messages = llm_service.round_messages[1]
        self.assertEqual(
            continued_messages[1]["provider_assistant_content"],
            llm_service.provider_content,
        )
        _, converted = ClaudeMessageConverter.convert(continued_messages)
        self.assertEqual(converted[1]["content"], llm_service.provider_content)
        self.assertEqual(converted[2]["role"], "user")
        self.assertEqual(converted[2]["content"][0]["type"], "tool_result")
        self.assertEqual(
            result.text,
            "The current guidance and your documents agree.",
        )
