"""Tests for the agentic tool-loop context framing."""

from django.test import SimpleTestCase

from mcp.services.tool_result_context import tool_result_context_builder


class ToolResultContextTests(SimpleTestCase):
    RESULTS = [{"tool_name": "quillmark__create_document", "result": "diag: bad field"}]

    def test_final_round_forbids_tools(self):
        text = tool_result_context_builder.build(self.RESULTS, final=True)
        self.assertIn("Do not call additional tools", text)
        self.assertIn("quillmark__create_document", text)

    def test_continuing_round_demands_retry(self):
        text = tool_result_context_builder.build(self.RESULTS, final=False)
        self.assertIn("CALL THE TOOL AGAIN", text)
        self.assertNotIn("Do not call additional tools", text)

    def test_default_is_final(self):
        self.assertIn(
            "Do not call additional tools",
            tool_result_context_builder.build(self.RESULTS),
        )
