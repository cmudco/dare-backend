"""Base interface for MCP transport clients."""

from abc import ABC, abstractmethod


class BaseMCPClient(ABC):
    """Common MCP client contract across local and remote transports."""

    @abstractmethod
    async def initialize(self) -> dict:
        """Initialize the MCP session."""
        pass

    @abstractmethod
    async def list_tools(self) -> list[dict]:
        """Return available MCP tools."""
        pass

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call one MCP tool."""
        pass

    @abstractmethod
    async def close(self):
        """Release transport resources."""
        pass

