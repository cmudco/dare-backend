from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from mcp.models import GatewayFetch, MCPServer, UserMCPConnection
from mcp.services.mcp_tool_executor import MCPToolExecutorError
from research.models import ResearchProject
from users.constants import RoleChoice

User = get_user_model()
GATEWAY = "/mcp/api/gateway/"
SEARCH_TOOL = {
    "name": "search",
    "description": "Search papers with citation context",
    "inputSchema": {"type": "object", "properties": {"term": {"type": "string"}}},
}


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class ConnectedToolTests(TestCase):
    """A project's agent sees and calls the owner's connected tools live."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.project = ResearchProject.objects.create(
            user=self.user, title="P", enabled_tools=["web", "scite"]
        )
        # Scite is in the seeded catalog.
        server = MCPServer.all_objects.get(slug="scite")
        UserMCPConnection.all_objects.create(
            user=self.user, server=server, cached_tools=[SEARCH_TOOL]
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def rpc(self, method, params=None, project=None, session=None):
        headers = {}
        if project is not None:
            headers["HTTP_X_DARE_PROJECT"] = str(project.id)
        if session is not None:
            headers["HTTP_MCP_SESSION_ID"] = session
        return self.client.post(
            GATEWAY,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            format="json",
            **headers,
        )

    def tool_names(self, project=None):
        response = self.rpc("tools/list", project=project)
        return {tool["name"] for tool in response.json()["result"]["tools"]}

    def test_an_enabled_connection_is_listed_for_its_project_only(self):
        self.assertIn("scite__search", self.tool_names(self.project))
        self.assertNotIn("scite__search", self.tool_names())

        self.project.enabled_tools = ["web"]
        self.project.save()
        self.assertNotIn("scite__search", self.tool_names(self.project))

    def test_another_users_project_does_not_unlock_tools(self):
        stranger = User.objects.create_user(
            email="s@example.com", password="pw", platform_role=RoleChoice.RESEARCHER
        )
        client = APIClient()
        client.force_authenticate(stranger)
        response = client.post(
            GATEWAY,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            format="json",
            HTTP_X_DARE_PROJECT=str(self.project.id),
        )
        names = {t["name"] for t in response.json()["result"]["tools"]}
        self.assertNotIn("scite__search", names)

    def test_a_call_runs_with_the_owners_connection_and_is_recorded(self):
        result = {"content": [{"type": "text", "text": "Paper A (2024)"}]}
        with patch(
            "mcp.services.mcp_gateway.mcp_tool_executor.execute_tool_call",
            return_value=result,
        ) as execute:
            response = self.rpc(
                "tools/call",
                {"name": "scite__search", "arguments": {"term": "x", "run": "r1-x"}},
                project=self.project,
            )

        execute.assert_called_once_with(self.user, "scite", "search", {"term": "x"})
        self.assertEqual(
            response.json()["result"]["content"][0]["text"], "Paper A (2024)"
        )
        self.assertTrue(
            GatewayFetch.all_objects.filter(
                tool="scite__search", content="Paper A (2024)"
            ).exists()
        )

    def test_a_failing_tool_is_reported_as_text_not_an_mcp_error(self):
        with patch(
            "mcp.services.mcp_gateway.mcp_tool_executor.execute_tool_call",
            side_effect=MCPToolExecutorError("token expired"),
        ):
            response = self.rpc(
                "tools/call",
                {"name": "scite__search", "arguments": {"term": "x"}},
                project=self.project,
            )

        result = response.json()["result"]
        self.assertNotIn("isError", result)
        self.assertIn("token expired", result["content"][0]["text"])

    def test_a_changed_tool_set_is_announced_until_listed_again(self):
        session = self.rpc("initialize", project=self.project)["Mcp-Session-Id"]
        self.rpc("tools/list", project=self.project, session=session)
        # A new session is announced once, so a reconnected client refreshes.
        first = self.rpc("ping", project=self.project, session=session)
        self.assertEqual(first["Content-Type"], "text/event-stream")
        self.rpc("tools/list", project=self.project, session=session)
        quiet = self.rpc("ping", project=self.project, session=session)
        self.assertEqual(quiet.json()["result"], {})

        self.project.enabled_tools = ["web"]
        self.project.save()

        announced = self.rpc("ping", project=self.project, session=session)
        self.assertEqual(announced["Content-Type"], "text/event-stream")
        body = announced.content.decode()
        self.assertLess(
            body.index("notifications/tools/list_changed"), body.index('"result"')
        )

        listed = self.rpc("tools/list", project=self.project, session=session)
        self.assertNotIn(
            "scite__search", {t["name"] for t in listed.json()["result"]["tools"]}
        )
        self.assertEqual(
            self.rpc("ping", project=self.project, session=session).json()["result"],
            {},
        )

    def test_an_unknown_session_must_start_again(self):
        response = self.rpc("tools/list", project=self.project, session="gone")
        self.assertEqual(response.status_code, 404)
