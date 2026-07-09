"""Tool-call DTOs shared by the streaming layer and the tool loop."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ToolCallRequest:
    """A complete tool call emitted by the model.

    Attributes:
        id: Provider-assigned call id. Empty for providers that do not
            assign ids (Gemini); the tool loop synthesizes one before the
            call is exposed anywhere.
        name: Tool function name (MCP tools keep their ``server__tool`` form).
        arguments: Raw JSON string of the call arguments, exactly as the
            provider streamed it.
    """

    id: str
    name: str
    arguments: str

    def to_payload_dict(self) -> Dict[str, str]:
        """Legacy dict shape consumed by the tool handlers."""
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    def with_id(self, new_id: str) -> "ToolCallRequest":
        return ToolCallRequest(id=new_id, name=self.name, arguments=self.arguments)


@dataclass(frozen=True)
class ToolCallResult:
    """Outcome of executing one tool call.

    ``content`` is the text the model sees in the ``role:"tool"`` turn;
    ``raw_result`` keeps the executor's original payload for persistence
    and FE events.
    """

    tool_call_id: str
    tool_name: str
    origin: str  # conversations.constants.ToolCallOrigin value
    server_slug: str
    content: str
    is_error: bool = False
    raw_result: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ToolLoopConfig:
    """Bounds for the tool loop."""

    max_rounds: int
