from django.test import SimpleTestCase

from mcp.services.mcp_client import MCPProtocolError
from mcp.services.streamable_http_client import StreamableHTTPMCPClient


class SSEResponseTests(SimpleTestCase):
    def setUp(self):
        self.client = StreamableHTTPMCPClient.__new__(StreamableHTTPMCPClient)

    def test_the_response_is_found_after_a_notification_event(self):
        body = (
            'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress",'
            '"params":{"progress":1}}\n\n'
            'event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"ok":true}}\n\n'
        )

        self.assertEqual(
            self.client._parse_sse_body(body),
            {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}},
        )

    def test_a_message_split_over_data_lines_is_joined(self):
        body = 'data: {"jsonrpc":"2.0","id":1,\ndata: "result":{}}\n\n'

        self.assertEqual(self.client._parse_sse_body(body)["id"], 1)

    def test_a_stream_without_a_response_is_an_error(self):
        body = 'data: {"jsonrpc":"2.0","method":"notifications/message"}\n\n'

        with self.assertRaises(MCPProtocolError):
            self.client._parse_sse_body(body)
