"""Build model-facing context from completed tool calls."""

from typing import Dict, List


class ToolResultContextBuilder:
    """Formats raw tool results for the follow-up LLM call.

    Two modes, matching the agentic tool loop in MCPToolHandler:

    - Continuing rounds (``final=False``): tools are still attached. The model
      is told to finish the task — including correcting failed tool calls and
      calling the tool again — before answering in text. Without this, a model
      that receives a validation error tends to print the corrected document
      into chat instead of re-invoking the tool.
    - Final round (``final=True``): tools are stripped; the model must answer
      from the results it has.
    """

    def build(
        self,
        tool_results: List[Dict],
        final: bool = True,
        original_request: str = "",
    ) -> str:
        if final:
            header = (
                "External tool results are below.",
                "Answer the user's original request using these results. "
                "Do not call additional tools.",
            )
        else:
            header = (
                "External tool results so far are below. The task may not be "
                "complete.",
                "If a tool call failed or returned diagnostics, correct the "
                "input and CALL THE TOOL AGAIN now — do not paste the "
                "corrected content into the chat; content only counts when it "
                "goes through the tool. If further tool calls are needed to "
                "finish the user's request (e.g. get_spec then "
                "create_document), make them. Only reply in plain text once "
                "the task is fully done, and then briefly summarize the "
                "outcome for the user.",
            )

        parts: List[str] = []
        # The follow-up request replaces the user's message with this context,
        # and on a conversation's first exchange the original request is not
        # yet in persisted history — restate it so the model can always see
        # what it is answering.
        if original_request:
            parts.append(
                "The user's request being handled:\n" f"{original_request}"
            )
        parts.extend(header)
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
