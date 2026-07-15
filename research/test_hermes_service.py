from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from research.services.hermes_service import HermesService, HermesStopResult


@override_settings(
    HERMES_GATEWAY_URL="http://hermes.test:8642",
    HERMES_API_KEY="test-key",
)
class HermesServiceContractTests(SimpleTestCase):
    def setUp(self):
        self.hermes = HermesService()

    @patch("research.services.hermes_service.requests.post")
    def test_start_run_sends_the_supported_upstream_request(self, post):
        post.return_value.json.return_value = {
            "run_id": "run_123",
            "status": "started",
        }

        result = self.hermes.start_run(
            input_text="task",
            instructions="standards",
            session_id="session-1",
            session_key="project-1",
        )

        post.assert_called_once_with(
            "http://hermes.test:8642/v1/runs",
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
                "X-Hermes-Session-Key": "project-1",
            },
            json={
                "input": "task",
                "instructions": "standards",
                "session_id": "session-1",
            },
            timeout=30,
        )
        post.return_value.raise_for_status.assert_called_once_with()
        self.assertEqual(result, {"run_id": "run_123", "status": "started"})

    @patch("research.services.hermes_service.requests.get")
    def test_stream_events_parses_only_sse_data_lines(self, get):
        response = MagicMock()
        response.iter_lines.return_value = iter(
            [
                ": keepalive",
                'data: {"event":"message.delta","delta":"hello"}',
                "",
                'data: {"event":"run.completed","usage":{"total_tokens":9}}',
            ]
        )
        get.return_value.__enter__.return_value = response

        events = list(self.hermes.stream_events("run_123"))

        get.assert_called_once_with(
            "http://hermes.test:8642/v1/runs/run_123/events",
            headers={"Authorization": "Bearer test-key"},
            stream=True,
            timeout=300,
        )
        self.assertEqual(
            events,
            [
                {"event": "message.delta", "delta": "hello"},
                {"event": "run.completed", "usage": {"total_tokens": 9}},
            ],
        )

    @patch("research.services.hermes_service.requests.get")
    def test_get_run_returns_terminal_hermes_data(self, get):
        terminal = {
            "run_id": "run_123",
            "status": "completed",
            "output": "answer",
            "usage": {"total_tokens": 9},
            "session_id": "session-1",
            "model": "hermes-agent",
        }
        get.return_value.json.return_value = terminal

        self.assertEqual(self.hermes.get_run("run_123"), terminal)
        get.assert_called_once_with(
            "http://hermes.test:8642/v1/runs/run_123",
            headers={"Authorization": "Bearer test-key"},
            timeout=30,
        )

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_returns_acknowledgement(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "run_id": "run_123",
            "status": "stopping",
        }

        result = self.hermes.stop_run("run_123")

        post.assert_called_once_with(
            "http://hermes.test:8642/v1/runs/run_123/stop",
            headers={"Authorization": "Bearer test-key"},
            timeout=15,
        )
        self.assertEqual(
            result,
            HermesStopResult(
                code="stop_acknowledged",
                acknowledged=True,
                http_status=200,
                upstream_status="stopping",
            ),
        )

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_distinguishes_not_found(self, post):
        post.return_value.status_code = 404

        result = self.hermes.stop_run("run_123")

        self.assertEqual(result.code, "hermes_run_not_found")
        self.assertEqual(result.http_status, 404)
        self.assertFalse(result.acknowledged)

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_distinguishes_timeout(self, post):
        post.side_effect = requests.Timeout("secret response text")

        result = self.hermes.stop_run("run_123")

        self.assertEqual(result.code, "stop_timeout")
        self.assertNotIn("secret", result.detail)

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_distinguishes_connection_failure(self, post):
        post.side_effect = requests.ConnectionError("connection refused")

        result = self.hermes.stop_run("run_123")

        self.assertEqual(result.code, "stop_connection_failure")

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_distinguishes_invalid_json(self, post):
        post.return_value.status_code = 200
        post.return_value.json.side_effect = ValueError("invalid")

        result = self.hermes.stop_run("run_123")

        self.assertEqual(result.code, "stop_invalid_json")
        self.assertEqual(result.http_status, 200)

    @patch("research.services.hermes_service.requests.post")
    def test_stop_run_distinguishes_upstream_5xx(self, post):
        post.return_value.status_code = 503

        result = self.hermes.stop_run("run_123")

        self.assertEqual(result.code, "stop_upstream_error")
        self.assertEqual(result.http_status, 503)
