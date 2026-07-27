from types import SimpleNamespace

from django.test import SimpleTestCase

from core.services.llm_helpers.tool_turn_helpers import (
    _history_result_text, build_history_tool_turns)


def _row(**overrides):
    defaults = dict(
        status="completed",
        result=None,
        error=None,
        tool_call_id="call-1",
        tool_name="quillmark__render_document",
        origin="mcp",
        server_slug="quillmark",
        round_index=1,
        arguments={},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class HistoryResultTextTests(SimpleTestCase):
    def test_empty_completed_result_gets_placeholder(self):
        # Regression: legacy MCP rows persisted NULL results for completed
        # calls; an empty string here gets dropped by the empty-content
        # message filter and orphans the parent tool_calls turn (provider 400).
        self.assertEqual(
            _history_result_text(_row(result=None)),
            "(tool completed with no output)",
        )

    def test_whitespace_only_result_gets_placeholder(self):
        self.assertEqual(
            _history_result_text(_row(result="   \n")),
            "(tool completed with no output)",
        )

    def test_failed_row_keeps_its_error(self):
        text = _history_result_text(_row(status="failed", error="boom"))
        self.assertEqual(text, "Error: boom")

    def test_long_result_is_truncated_with_note(self):
        text = _history_result_text(_row(result="x" * 5000))
        self.assertIn("[truncated, 5000 total chars]", text)


class HistoryTurnReplayTests(SimpleTestCase):
    def test_every_tool_call_id_keeps_a_nonempty_result_turn(self):
        rows = [
            _row(tool_call_id="call-1", result=None),
            _row(tool_call_id="call-2", result="{}", round_index=2),
        ]
        turns = build_history_tool_turns(7, rows)

        assistant_ids = [
            call["id"]
            for turn in turns
            if turn["role"] == "assistant"
            for call in turn.get("tool_calls", [])
        ]
        tool_turns = {
            turn["tool_call_id"]: turn for turn in turns if turn["role"] == "tool"
        }

        self.assertEqual(sorted(assistant_ids), sorted(tool_turns.keys()))
        for turn in tool_turns.values():
            self.assertTrue(
                turn["content"].strip(),
                f"tool turn {turn['tool_call_id']} has empty content — it "
                "would be dropped by the history filter and orphan its call",
            )
