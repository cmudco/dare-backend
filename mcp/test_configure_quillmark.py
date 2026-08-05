from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from mcp.constants import MCPAuthType, MCPTransport
from mcp.models import MCPServer


class ConfigureQuillmarkCommandTests(TestCase):
    def setUp(self):
        self.server, _ = MCPServer.all_objects.update_or_create(
            slug="quillmark",
            defaults={
                "name": "CMU Documents",
                "transport": MCPTransport.STREAMABLE_HTTP,
                "auth_type": MCPAuthType.NONE,
                "remote_url": "http://quillmark-mcp:8080/mcp",
                "command": "",
            },
        )

    def test_configures_host_reachable_url_and_activates_server(self):
        output = StringIO()
        call_command(
            "configure_quillmark",
            url="http://127.0.0.1:8090/mcp",
            stdout=output,
        )

        self.server.refresh_from_db()
        self.assertEqual(self.server.remote_url, "http://127.0.0.1:8090/mcp")
        self.assertTrue(self.server.is_active)
        self.assertIn("Quillmark is active", output.getvalue())

    def test_rejects_non_http_url(self):
        with self.assertRaises(CommandError):
            call_command("configure_quillmark", url="file:///etc/passwd")
