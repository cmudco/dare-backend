"""
Tool Fetcher Service

Unified interface for fetching tool definitions from all sources (MCP servers,
DARE native tools, etc.) without creating circular dependencies.

This module uses lazy imports for external tool systems to avoid import-time
circular dependencies. The imports happen at method call time, not module load.

Usage:
    from core.integrations import ToolFetcher

    fetcher = ToolFetcher()
    tools = await fetcher.get_all_tools(request, llm)
"""

import logging
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


class ToolFetcher:
    """
    Unified interface for fetching tool definitions from all sources.

    This class centralizes tool fetching logic that was previously spread
    across LLMService. It uses lazy imports to avoid circular dependencies
    with mcp.services and dare_tools.services.

    Responsibilities:
    - Fetch MCP tools from user's connected servers
    - Fetch DARE native tools (diagrams, charts, etc.)
    - Combine tools for LLM requests
    """

    async def get_mcp_tools(
        self,
        user,
        server_ids: Optional[Set[int]],
        llm_provider: str,
    ) -> List:
        """
        Fetch MCP tools from specified servers.

        Args:
            user: User instance (required for MCP connection lookup)
            server_ids: Set of MCP server IDs to fetch tools from
            llm_provider: LLM provider string for format conversion

        Returns:
            List of tool definitions in LLM-compatible format
        """
        if not server_ids or not user:
            return []

        try:
            # Lazy import to avoid circular dependency
            # (mcp.services modules import from core.services)
            from mcp.services import MCPToolExecutor

            executor = MCPToolExecutor()
            tools = await executor.get_tools_for_server_ids(
                user=user,
                server_ids=list(server_ids),
                llm_provider=llm_provider,
            )

            if tools:
                logger.info(f"[ToolFetcher] Loaded {len(tools)} MCP tools")

            return tools
        except Exception as e:
            logger.warning(f"[ToolFetcher] Failed to get MCP tools: {e}")
            return []

    def get_dare_tools(
        self,
        tool_slugs: Optional[Set[str]],
        provider: str,
    ) -> List:
        """
        Fetch DARE native tools from the registry.

        Args:
            tool_slugs: Set of DARE tool slugs to fetch
            provider: LLM provider string for format conversion

        Returns:
            List of tool definitions in LLM-compatible format
        """
        if not tool_slugs:
            return []

        try:
            # Lazy import to keep this module independent
            from dare_tools.services.registry import get_dare_tool_schemas

            tools = get_dare_tool_schemas(
                tool_slugs=list(tool_slugs),
                provider=provider,
            )

            if tools:
                logger.info(f"[ToolFetcher] Loaded {len(tools)} DARE tools")

            return tools
        except Exception as e:
            logger.warning(f"[ToolFetcher] Failed to get DARE tools: {e}")
            return []

    async def get_all_tools(
        self,
        request,
        llm,
        external_tools: Optional[List] = None,
    ) -> List:
        """
        Get all tools for a request (MCP + DARE + external).

        Combines tools from all sources into a single list for the LLM.

        Args:
            request: LLMQueryRequest with tool configuration
            llm: LLM instance (used for provider-specific formatting)
            external_tools: Optional pre-defined tools to include

        Returns:
            Combined list of all tool definitions
        """
        all_tools = list(external_tools) if external_tools else []

        # Fetch MCP tools if server IDs provided
        if request.requires_mcp_tools():
            logger.info(f"[ToolFetcher] Request has MCP server IDs: {request.mcp_server_ids}")
            mcp_tools = await self.get_mcp_tools(
                user=request.user,
                server_ids=request.mcp_server_ids,
                llm_provider=llm.provider,
            )
            all_tools.extend(mcp_tools)
        else:
            logger.debug("[ToolFetcher] No MCP server IDs in request")

        # Fetch DARE tools if slugs provided
        if request.requires_dare_tools():
            logger.info(f"[ToolFetcher] Request has DARE tool slugs: {request.dare_tool_slugs}")
            dare_tools = self.get_dare_tools(
                tool_slugs=request.dare_tool_slugs,
                provider=llm.provider,
            )
            all_tools.extend(dare_tools)
        else:
            logger.debug("[ToolFetcher] No DARE tool slugs in request")

        # Log combined tools
        if all_tools:
            tool_names = [t.get('function', {}).get('name', 'unknown') for t in all_tools]
            logger.info(f"[ToolFetcher] Passing {len(all_tools)} tools to LLM: {tool_names}")

        return all_tools
