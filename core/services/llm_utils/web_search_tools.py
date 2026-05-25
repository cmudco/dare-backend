"""
Web search tool definitions for LLM providers.

This module provides functions to get web search tool configurations
for different LLM providers.
"""

from typing import Dict, List, Optional

from google.genai import types


class WebSearchTools:
    """Web search tool definitions for different providers."""

    @staticmethod
    def has_web_search(tools: Optional[List[Dict]]) -> bool:
        """
        Check if tools list contains web search indicator.

        Args:
            tools: List of tool dictionaries

        Returns:
            True if web search is enabled
        """
        if not tools:
            return False
        return any(t.get("type") == "web_search" for t in tools)


class OpenAIWebSearchTools:
    """OpenAI-specific web search tools."""

    @staticmethod
    def get_tool_definition() -> Dict:
        """
        Get web search tool indicator for OpenAI.

        OpenAI uses the Responses API with tools=[{"type": "web_search"}].
        Supported on all models via the Responses API.

        Returns:
            Web search tool dictionary
        """
        return {"type": "web_search"}


class ClaudeWebSearchTools:
    """Claude-specific web search tools."""

    @staticmethod
    def get_tool_definition() -> Dict:
        """
        Get the native web search tool definition for Claude API.

        Returns:
            Web search tool dictionary
        """
        return {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5
        }


class ClaudeWebFetchTools:
    """Claude-specific web fetch tool."""

    BETA_HEADER = "web-fetch-2025-09-10"
    TOOL_TYPE = "web_fetch_20250910"

    @classmethod
    def get_tool_definition(cls) -> Dict:
        """
        Get the native web fetch tool definition for Claude API.

        Returns:
            Web fetch tool dictionary
        """
        return {
            "type": cls.TOOL_TYPE,
            "name": "web_fetch",
            "max_uses": 3,
            "citations": {"enabled": True},
            "max_content_tokens": 50000,
        }

    @classmethod
    def has_web_fetch(cls, tools: Optional[List[Dict]]) -> bool:
        if not tools:
            return False
        return any(str(t.get("type", "")).startswith("web_fetch_") for t in tools)


class GeminiWebSearchTools:
    """Gemini-specific web search tools."""

    @staticmethod
    def get_tool_definition() -> Dict:
        """
        Get the native Google Search tool definition for Gemini API.

        Returns:
            Web search tool dictionary
        """
        return {"google_search": {}}

    @staticmethod
    def has_google_search(tools: Optional[List[Dict]]) -> bool:
        """
        Check if tools list contains Google Search.

        Args:
            tools: List of tool dictionaries or native Tool objects

        Returns:
            True if google_search is present
        """
        if not tools:
            return False
            
        for tool in tools:
            # Handle native Gemini Tool object
            if hasattr(tool, 'google_search') and tool.google_search is not None:
                return True
            # Handle dictionary format
            if isinstance(tool, dict) and "google_search" in tool:
                return True
                
        return False

    @staticmethod
    def build_google_search_tool():
        """
        Build Gemini Google Search tool object.

        Returns:
            Gemini Tool object with Google Search
        """
        return types.Tool(google_search=types.GoogleSearch())
