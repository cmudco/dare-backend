from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from research import tasks
from research.constants import AgentRunStatus
from research.tasks import _stream_run


class StreamRunOwnershipTests(SimpleTestCase):
    @staticmethod
    def _run(run_id="run_test"):
        return SimpleNamespace(
            hermes_run_id=run_id,
            status=AgentRunStatus.RUNNING,
            status_detail="",
            completed_at=None,
            usage={},
            save=MagicMock(),
        )

    def test_dare_does_not_count_or_stop_after_many_tool_events(self):
        events = []
        for index in range(25):
            events.extend(
                [
                    {"event": "tool.started", "preview": f"query-{index}"},
                    {"event": "tool.completed", "tool": "web_search"},
                ]
            )
        events.extend(
            [
                {"event": "message.delta", "delta": "done"},
                {"event": "run.completed"},
            ]
        )
        hermes = SimpleNamespace(stream_events=lambda _run_id: iter(events))
        run = self._run()
        completed_tools = []

        output = _stream_run(
            hermes,
            run,
            "interrupted",
            lambda event, preview: completed_tools.append((event, preview)),
        )

        self.assertEqual(output, "done")
        self.assertEqual(len(completed_tools), 25)
        self.assertEqual(completed_tools[-1][1], "query-24")

    def test_completed_terminal_get_recovers_output_after_stream_exception(self):
        def interrupted_stream(_run_id):
            yield {"event": "message.delta", "delta": "partial"}
            raise TimeoutError("SSE read timed out")

        hermes = SimpleNamespace(
            stream_events=interrupted_stream,
            get_run=lambda _run_id: {
                "status": "completed",
                "output": "terminal output",
            },
        )

        output = _stream_run(hermes, self._run(), "The run was interrupted.")

        self.assertEqual(output, "terminal output")

    def test_confirmed_hermes_failure_fails_the_dare_run(self):
        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter([]),
            get_run=lambda _run_id: {
                "status": "failed",
                "error": "provider unavailable",
            },
        )
        run = self._run("run_failed")

        with patch("research.tasks._fail") as fail:
            output = _stream_run(hermes, run, "The run was interrupted.")

        self.assertIsNone(output)
        fail.assert_called_once()
        self.assertEqual(str(fail.call_args.args[2]), "provider unavailable")

    def test_active_status_after_stream_interruption_is_not_failed(self):
        def interrupted_stream(_run_id):
            raise ConnectionError("connection reset")
            yield  # pragma: no cover - makes this a generator

        for status in ("queued", "running", "waiting_for_approval", "stopping"):
            with self.subTest(status=status):
                run = self._run(f"run_{status}")
                hermes = SimpleNamespace(
                    stream_events=interrupted_stream,
                    get_run=lambda _run_id, value=status: {"status": value},
                )

                with patch("research.tasks._fail") as fail:
                    output = _stream_run(hermes, run, "The run was interrupted.")

                self.assertIsNone(output)
                fail.assert_not_called()
                self.assertEqual(run.status, status)
                self.assertIsNone(run.completed_at)

    def test_unavailable_terminal_truth_is_outcome_unknown(self):
        run = self._run("run_unknown")

        def unavailable(_run_id):
            raise RuntimeError("404 run not found")

        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter([]),
            get_run=unavailable,
        )

        with patch("research.tasks._fail") as fail:
            output = _stream_run(hermes, run, "The run was interrupted.")

        self.assertIsNone(output)
        fail.assert_not_called()
        self.assertEqual(run.status, AgentRunStatus.OUTCOME_UNKNOWN)
        self.assertIsNone(run.completed_at)

    def test_empty_stream_uses_terminal_get_output(self):
        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter([]),
            get_run=lambda _run_id: {"status": "completed", "output": "recovered"},
        )

        self.assertEqual(
            _stream_run(hermes, self._run("run_empty"), "interrupted"),
            "recovered",
        )

    def test_terminal_event_without_message_deltas_uses_terminal_get_output(self):
        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter([{"event": "run.completed"}]),
            get_run=lambda _run_id: {
                "status": "completed",
                "output": "terminal-only",
            },
        )

        self.assertEqual(
            _stream_run(hermes, self._run("run_no_deltas"), "interrupted"),
            "terminal-only",
        )

    def test_stream_without_terminal_event_uses_terminal_get_output(self):
        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter(
                [{"event": "message.delta", "delta": "partial"}]
            ),
            get_run=lambda _run_id: {"status": "completed", "output": "complete"},
        )

        self.assertEqual(
            _stream_run(hermes, self._run("run_no_terminal"), "interrupted"),
            "complete",
        )

    def test_cancelled_terminal_status_is_preserved(self):
        run = self._run("run_cancelled")
        hermes = SimpleNamespace(
            stream_events=lambda _run_id: iter([{"event": "run.cancelled"}]),
            get_run=lambda _run_id: {
                "status": "cancelled",
                "usage": {"total_tokens": 12},
            },
        )

        output = _stream_run(hermes, run, "interrupted")

        self.assertIsNone(output)
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(run.usage, {"total_tokens": 12})

    def test_automatic_repair_execution_is_removed(self):
        self.assertFalse(hasattr(tasks, "_reask_json"))
