from types import SimpleNamespace
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from conversations.api.serializers import ConversationSummarySerializer
from conversations.services.message_coordinator import MessageCoordinator
from conversations.services.message_helpers.usage_helpers import UsageAccumulator
from conversations.services.tool_loop_service import ToolLoopResult
from core.services.dtos import StreamEventKind, ToolCallRequest
from core.services.llm_helpers.tool_turn_helpers import build_assistant_tool_call_turn
from core.services.llm_utils.provider_message_converters import ClaudeMessageConverter
from core.services.llm_utils.stream_processors import ClaudeStreamProcessor
from core.services.llm_utils.usage_extractors import ClaudeUsageExtractor


class ClaudeUsageObservabilityTests(SimpleTestCase):
    def test_summary_serializer_does_not_include_message_usage_details(self):
        self.assertNotIn("usage_details", ConversationSummarySerializer().fields)

    def test_extracts_thinking_and_approximate_non_thinking_tokens(self):
        extractor = ClaudeUsageExtractor()
        extractor.extract_from_message_start(
            SimpleNamespace(
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=25))
            )
        )

        usage = extractor.extract_from_message_delta(
            SimpleNamespace(
                usage=SimpleNamespace(
                    output_tokens=348,
                    output_tokens_details=SimpleNamespace(thinking_tokens=312),
                )
            )
        )

        self.assertEqual(usage["thinking_tokens"], 312)
        self.assertEqual(usage["visible_output_tokens"], 36)
        self.assertEqual(usage["output_tokens"], 348)

    async def test_final_stream_frame_includes_stop_reason(self):
        async def stream():
            yield SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=25)),
            )
            yield SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="max_tokens"),
                usage=SimpleNamespace(
                    output_tokens=348,
                    output_tokens_details=SimpleNamespace(thinking_tokens=312),
                ),
            )

        events = [
            event async for event in ClaudeStreamProcessor.process_stream(stream())
        ]

        self.assertEqual([event.kind for event in events], [StreamEventKind.USAGE] * 2)
        self.assertTrue(events[0].usage["provisional"])
        self.assertEqual(events[1].usage["stop_reason"], "max_tokens")
        self.assertEqual(events[1].usage["thinking_tokens"], 312)

    async def test_streams_provider_thinking_summary_separately_from_answer(self):
        async def stream():
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="thinking_delta", thinking="Checking the evidence…"
                ),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="Final answer"),
            )

        events = [
            event async for event in ClaudeStreamProcessor.process_stream(stream())
        ]

        self.assertEqual(
            [event.kind for event in events],
            [StreamEventKind.THINKING_DELTA, StreamEventKind.TEXT_DELTA],
        )
        self.assertEqual(events[0].text, "Checking the evidence…")

    async def test_preserves_signed_thinking_block_for_tool_continuation(self):
        async def stream():
            yield SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(
                    type="thinking", thinking="", signature=""
                ),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="thinking_delta", thinking="Check the source."
                ),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="signature_delta", signature="signed"),
            )
            yield SimpleNamespace(type="content_block_stop")

        events = [
            event async for event in ClaudeStreamProcessor.process_stream(stream())
        ]
        ready = next(
            event
            for event in events
            if event.kind is StreamEventKind.THINKING_BLOCK_READY
        )
        turn = build_assistant_tool_call_turn(
            "",
            [ToolCallRequest(id="tool-1", name="search", arguments="{}")],
            provider_thinking_blocks=[
                {
                    "type": "thinking",
                    "thinking": ready.text,
                    "signature": ready.thinking_signature,
                }
            ],
        )

        _, converted = ClaudeMessageConverter.convert([turn])

        self.assertEqual(
            converted[0]["content"][0],
            {
                "type": "thinking",
                "thinking": "Check the source.",
                "signature": "signed",
            },
        )

    async def test_preserves_redacted_thinking_block_for_tool_continuation(self):
        async def stream():
            yield SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(
                    type="redacted_thinking", data="opaque-redacted-data"
                ),
            )
            yield SimpleNamespace(type="content_block_stop")

        events = [
            event async for event in ClaudeStreamProcessor.process_stream(stream())
        ]
        ready = next(
            event
            for event in events
            if event.kind is StreamEventKind.THINKING_BLOCK_READY
        )
        turn = build_assistant_tool_call_turn(
            "",
            [ToolCallRequest(id="tool-1", name="search", arguments="{}")],
            provider_thinking_blocks=[ready.provider_thinking_block],
        )

        _, converted = ClaudeMessageConverter.convert([turn])

        self.assertEqual(
            converted[0]["content"][0],
            {"type": "redacted_thinking", "data": "opaque-redacted-data"},
        )

    def test_replays_complete_interleaved_assistant_response_unchanged(self):
        provider_content = [
            {
                "type": "thinking",
                "thinking": "Check the first source.",
                "signature": "signed-1",
            },
            {
                "type": "server_tool_use",
                "id": "srvtoolu-1",
                "name": "web_search",
                "input": {"query": "first"},
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
            {"type": "text", "text": "Now use the client tool."},
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "search",
                "input": {"query": "second"},
            },
        ]
        turn = build_assistant_tool_call_turn(
            "Now use the client tool.",
            [
                ToolCallRequest(
                    id="tool-1",
                    name="search",
                    arguments='{"query":"second"}',
                )
            ],
            provider_assistant_content=provider_content,
        )

        _, converted = ClaudeMessageConverter.convert([turn])

        self.assertEqual(converted[0]["content"], provider_content)
        self.assertIsNot(converted[0]["content"], provider_content)

    async def test_stream_exposes_provider_blocks_with_original_indexes(self):
        class ReplayBlock(SimpleNamespace):
            def model_dump(self, **_kwargs):
                return dict(vars(self))

        async def stream():
            yield SimpleNamespace(
                type="content_block_start",
                index=4,
                content_block=ReplayBlock(type="thinking", thinking="", signature=""),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                index=4,
                delta=SimpleNamespace(type="thinking_delta", thinking="Check."),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                index=4,
                delta=SimpleNamespace(type="signature_delta", signature="signed"),
            )
            yield SimpleNamespace(type="content_block_stop", index=4)
            yield SimpleNamespace(
                type="content_block_start",
                index=5,
                content_block=ReplayBlock(type="text", text=""),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                index=5,
                delta=SimpleNamespace(type="text_delta", text="Visible"),
            )
            yield SimpleNamespace(type="content_block_stop", index=5)
            yield SimpleNamespace(
                type="content_block_start",
                index=6,
                content_block=ReplayBlock(
                    type="server_tool_use",
                    id="srvtoolu-1",
                    name="web_search",
                    input={},
                ),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                index=6,
                delta=SimpleNamespace(
                    type="input_json_delta", partial_json='{"query":"current"}'
                ),
            )
            yield SimpleNamespace(type="content_block_stop", index=6)
            yield SimpleNamespace(
                type="content_block_start",
                index=7,
                content_block=ReplayBlock(
                    type="web_search_tool_result",
                    tool_use_id="srvtoolu-1",
                    content=[],
                ),
            )
            yield SimpleNamespace(type="content_block_stop", index=7)

        events = [
            event async for event in ClaudeStreamProcessor.process_stream(stream())
        ]
        replay_events = [
            event
            for event in events
            if event.kind is StreamEventKind.PROVIDER_CONTENT_BLOCK_READY
        ]

        self.assertEqual(
            [event.provider_block_index for event in replay_events], [4, 5, 6, 7]
        )
        self.assertEqual(
            [event.provider_content_block for event in replay_events],
            [
                {"type": "thinking", "thinking": "Check.", "signature": "signed"},
                {"type": "text", "text": "Visible"},
                {
                    "type": "server_tool_use",
                    "id": "srvtoolu-1",
                    "name": "web_search",
                    "input": {"query": "current"},
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu-1",
                    "content": [],
                },
            ],
        )

    async def test_credit_interruption_persists_usage_before_finalizing(self):
        coordinator = object.__new__(MessageCoordinator)
        coordinator._save_usage_breakdown = AsyncMock()
        coordinator._handle_insufficient_balance = AsyncMock()
        message = SimpleNamespace(id=42)
        result = ToolLoopResult(
            text="partial answer",
            token_usage={"input_tokens": 10, "output_tokens": 20},
            usage_breakdown=[{"round": 1, "thinking_tokens": 12}],
            interrupted=True,
            error_response={"error": "insufficient_balance"},
        )

        await coordinator._finalize_interrupted_turn(message, result)

        coordinator._save_usage_breakdown.assert_awaited_once_with(
            message, result.usage_breakdown
        )
        coordinator._handle_insufficient_balance.assert_awaited_once_with(
            message,
            result.text,
            result.token_usage,
            result.error_response,
        )

    def test_accumulator_preserves_observability_per_round_and_totals(self):
        accumulator = UsageAccumulator()
        accumulator.observe(
            1,
            {
                "input_tokens": 25,
                "output_tokens": 348,
                "total_tokens": 373,
                "thinking_tokens": 312,
                "visible_output_tokens": 36,
                "stop_reason": "max_tokens",
                "request_max_tokens": 32768,
                "effort": "medium",
            },
        )

        totals = accumulator.totals()
        breakdown = accumulator.breakdown()

        self.assertEqual(totals["thinking_tokens"], 312)
        self.assertEqual(totals["visible_output_tokens"], 36)
        self.assertEqual(totals["stop_reason"], "max_tokens")
        self.assertEqual(breakdown[0]["request_max_tokens"], 32768)
        self.assertEqual(breakdown[0]["effort"], "medium")
