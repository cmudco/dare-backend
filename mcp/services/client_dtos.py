"""DTOs shared by MCP client implementations."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MCPConnectionConfig:
    """Resolved connection settings for one MCP call."""

    timeout: float
    tool_call_timeout: Optional[float] = None
    startup_delay: float = 10.0
    access_token: Optional[str] = None
    remote_headers: dict[str, str] = field(default_factory=dict)
