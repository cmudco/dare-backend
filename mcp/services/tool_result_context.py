"""Build model-facing context from completed tool calls."""

from typing import Dict, List


class ToolResultContextBuilder:
    """Formats raw tool results for the follow-up LLM call."""

    def build(self, tool_results: List[Dict]) -> str:
        parts = [
            "External tool results are below.",
            "Answer the user's original request using these results. "
            "Do not call additional tools.",
        ]

        for result in tool_results:
            tool_name = result.get("tool_name", "unknown_tool")
            tool_output = result.get("result") or ""
            parts.append(
                f"Tool: {tool_name}\n"
                "Result:\n"
                "```json\n"
                f"{tool_output}\n"
                "```"
            )

        return "\n\n".join(parts)


tool_result_context_builder = ToolResultContextBuilder()
