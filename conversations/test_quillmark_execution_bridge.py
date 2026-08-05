from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from conversations.services.tool_execution_service import ToolExecutionService
from mcp.services.artifact_bridge import BridgeResult, BridgeStatus


class QuillmarkExecutionBridgeTests(SimpleTestCase):
    async def test_pdf_result_is_bridged_and_internal_url_is_removed(self):
        service = ToolExecutionService()
        raw_result = {
            "content": [
                {
                    "type": "resource_link",
                    "uri": "http://quillmark-mcp:8080/artifacts/internal.pdf",
                    "mimeType": "application/pdf",
                }
            ],
            "structuredContent": {
                "url": "http://quillmark-mcp:8080/artifacts/internal.pdf",
                "mimeType": "application/pdf",
            },
        }
        bridged = {
            "artifact_id": 42,
            "title": "FY27 Memo",
            "filename": "fy27-memo.pdf",
            "version": 1,
        }
        ctx = SimpleNamespace(
            user=SimpleNamespace(),
            message=SimpleNamespace(),
            conversation=SimpleNamespace(),
            send_callback=AsyncMock(),
        )

        with (
            patch(
                "conversations.services.tool_execution_service."
                "mcp_tool_executor.execute_tool_call",
                new=AsyncMock(return_value=raw_result),
            ),
            patch(
                "conversations.services.tool_execution_service."
                "maybe_create_pdf_artifact",
                new=AsyncMock(
                    return_value=BridgeResult(BridgeStatus.CREATED, artifact=bridged)
                ),
            ) as bridge_mock,
        ):
            result, content, is_error = await service._execute_mcp(
                "quillmark",
                "create_document",
                {"content": "memo source"},
                ctx,
            )

        self.assertFalse(is_error)
        self.assertNotIn("quillmark-mcp", str(result))
        self.assertEqual(result["structuredContent"]["artifactId"], 42)
        self.assertIn("already displayed to the user", content)
        bridge_mock.assert_awaited_once()

    async def test_mcp_tool_error_remains_visible_to_the_loop(self):
        service = ToolExecutionService()
        raw_result = {
            "isError": True,
            "content": [{"type": "text", "text": "Missing required subject"}],
        }
        ctx = SimpleNamespace(
            user=SimpleNamespace(),
            message=SimpleNamespace(),
            conversation=SimpleNamespace(),
            send_callback=AsyncMock(),
        )

        with (
            patch(
                "conversations.services.tool_execution_service."
                "mcp_tool_executor.execute_tool_call",
                new=AsyncMock(return_value=raw_result),
            ),
            patch(
                "conversations.services.tool_execution_service."
                "maybe_create_pdf_artifact",
                new=AsyncMock(),
            ) as bridge_mock,
        ):
            result, content, is_error = await service._execute_mcp(
                "quillmark", "create_document", {}, ctx
            )

        self.assertTrue(is_error)
        self.assertTrue(result["isError"])
        self.assertEqual(content, "Missing required subject")
        bridge_mock.assert_not_awaited()

    async def test_bridge_failure_is_safe_and_visible_to_the_loop(self):
        service = ToolExecutionService()
        ctx = SimpleNamespace(
            user=SimpleNamespace(),
            message=SimpleNamespace(),
            conversation=SimpleNamespace(),
            send_callback=AsyncMock(),
        )
        raw_result = {
            "structuredContent": {
                "url": "http://internal/artifact.pdf",
                "mimeType": "application/pdf",
            }
        }
        outcome = BridgeResult(
            BridgeStatus.FAILED,
            error="The rendered PDF could not be imported into DARE.",
        )
        with (
            patch(
                "conversations.services.tool_execution_service."
                "mcp_tool_executor.execute_tool_call",
                new=AsyncMock(return_value=raw_result),
            ),
            patch(
                "conversations.services.tool_execution_service."
                "maybe_create_pdf_artifact",
                new=AsyncMock(return_value=outcome),
            ),
        ):
            result, content, is_error = await service._execute_mcp(
                "quillmark", "create_document", {}, ctx
            )

        self.assertTrue(is_error)
        self.assertNotIn("http://internal", str(result))
        self.assertEqual(content, outcome.error)
