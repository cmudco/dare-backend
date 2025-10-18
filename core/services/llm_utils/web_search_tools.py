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
        return len(tools) > 0


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
            tools: List of tool dictionaries

        Returns:
            True if google_search is present
        """
        if not tools:
            return False
        return any("google_search" in str(tool) for tool in tools)

    @staticmethod
    def build_google_search_tool():
        """
        Build Gemini Google Search tool object.

        Returns:
            Gemini Tool object with Google Search
        """
        return types.Tool(google_search=types.GoogleSearch())
