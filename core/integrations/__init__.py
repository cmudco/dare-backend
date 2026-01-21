"""
Core Integrations Package

This package provides integration bridges between core services and external
tool systems (MCP, DARE tools, etc.) without creating circular dependencies.

The key design principle: this package can import from external modules
(mcp.services, dare_tools.services) using lazy imports inside methods,
but external modules should NOT import from this package.
"""

from core.integrations.tool_fetcher import ToolFetcher

__all__ = ["ToolFetcher"]
