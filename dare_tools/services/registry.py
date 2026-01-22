"""
DARE Tools Registry.

Central registry for all internal DARE tools. Maps tool slugs to their
definitions and executors.
"""

import logging
from typing import Dict, List, Optional, Any, Callable

from core.services.llm_utils.diagram_tool import (
    get_diagram_tool_openai,
    get_diagram_tool_claude,
    json_to_mermaid,
)

logger = logging.getLogger(__name__)


# ============ TOOL EXECUTORS ============

def execute_create_diagram(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the create_diagram tool.
    
    Args:
        arguments: Dict with diagram_type, title, nodes, edges
        
    Returns:
        Dict with mermaid_code and metadata
    """
    try:
        mermaid_code = json_to_mermaid(arguments)
        return {
            "success": True,
            "mermaid_code": mermaid_code,
            "diagram_type": arguments.get("diagram_type", "flowchart"),
            "title": arguments.get("title", "Diagram"),
        }
    except Exception as e:
        logger.exception(f"Error executing create_diagram: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def execute_create_chart(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the create_chart tool.
    
    Args:
        arguments: Dict with chart_type, title, data, options
        
    Returns:
        Dict with chart configuration for frontend rendering
    """
    try:
        chart_type = arguments.get("chart_type", "bar")
        title = arguments.get("title", "Chart")
        data = arguments.get("data", [])
        options = arguments.get("options", {})
        
        # Return chart configuration for frontend to render
        return {
            "success": True,
            "chart_config": {
                "type": chart_type,
                "title": title,
                "data": data,
                "options": options,
            },
        }
    except Exception as e:
        logger.exception(f"Error executing create_chart: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ============ TOOL DEFINITIONS ============

def get_chart_tool_openai() -> Dict:
    """Get chart tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_chart",
            "description": "Create a NEW data visualization chart. Use this ONLY for creating brand new charts. If the user wants to MODIFY, UPDATE, or CHANGE an existing chart, you MUST use the update_artifact tool instead with the existing artifact_id. You MUST call this tool whenever the user asks for ANY type of NEW chart including: bar chart, line chart, pie chart, doughnut chart, area chart, or scatter chart. Do not describe the chart in text - always call this tool to render it visually.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "doughnut", "area", "scatter"],
                        "description": "Type of chart to create"
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the chart"
                    },
                    "data": {
                        "type": "array",
                        "description": "Data points for the chart. IMPORTANT: When updating charts, always preserve existing 'color' fields on data points.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Label for this data point"},
                                "value": {"type": "number", "description": "Numeric value"},
                                "color": {"type": "string", "description": "Color for this data point (hex like '#3B82F6' or name like 'blue'). Include this field to customize bar/slice colors."}
                            },
                            "required": ["label", "value"]
                        }
                    },
                    "dataKeys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of field names from data objects to chart (e.g., ['value', 'count']). These must match the keys in the data objects."
                    },
                    "xAxisKey": {
                        "type": "string",
                        "description": "Field name to use for x-axis labels (e.g., 'label', 'month', 'category'). Must match a key in the data objects."
                    },
                    "options": {
                        "type": "object",
                        "description": "Additional chart options",
                        "properties": {
                            "showLegend": {"type": "boolean", "description": "Show chart legend"},
                            "showLabels": {"type": "boolean", "description": "Show data labels"},
                            "xAxisLabel": {"type": "string", "description": "X-axis label"},
                            "yAxisLabel": {"type": "string", "description": "Y-axis label"}
                        }
                    }
                },
                "required": ["chart_type", "title", "data", "dataKeys", "xAxisKey"]
            }
        }
    }


def get_chart_tool_claude() -> Dict:
    """Get chart tool definition in Claude/Anthropic format."""
    openai_spec = get_chart_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"]
    }


def get_update_artifact_tool_openai() -> Dict:
    """Get update_artifact tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "update_artifact",
            "description": (
                "FULL REWRITE: Replace entire artifact content with new content. "
                "Use ONLY when making major changes affecting >30% of the content (restructuring, adding many elements, changing chart type). "
                "For SMALL edits (changing colors, fixing typos, updating single values), use update_artifact_inline instead - it's faster and more precise. "
                "You MUST reference the artifact_id of the artifact to update."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "integer",
                        "description": "The ID of the artifact to update. This must be a valid artifact ID from the current conversation."
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content for the artifact. For diagrams, provide the complete updated Mermaid code. For charts, provide the complete updated JSON configuration."
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional new title for the artifact. If not provided, keeps the original title."
                    }
                },
                "required": ["artifact_id", "content"]
            }
        }
    }


def get_update_artifact_tool_claude() -> Dict:
    """Get update_artifact tool definition in Claude/Anthropic format."""
    openai_spec = get_update_artifact_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"]
    }


def get_update_artifact_inline_tool_openai() -> Dict:
    """Get update_artifact_inline tool definition in OpenAI format.

    This tool enables targeted string replacement for small edits,
    similar to Claude's artifact update approach.
    """
    return {
        "type": "function",
        "function": {
            "name": "update_artifact_inline",
            "description": (
                "PREFERRED for small edits: Make targeted string replacement in an artifact. "
                "USE THIS for: changing colors (e.g., 'red' -> '#3B82F6'), fixing typos, updating values, small additions. "
                "Example: To change bar colors to blue, find the color values in the JSON and replace them. "
                "CRITICAL: old_str must be UNIQUE and match EXACTLY (including whitespace). "
                "If not unique, include more surrounding context. "
                "For major rewrites (>30% changes), use update_artifact instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "integer",
                        "description": "The ID of the artifact to modify. Must be a valid artifact ID from the current conversation."
                    },
                    "old_str": {
                        "type": "string",
                        "description": (
                            "The exact string to find and replace in the artifact content. "
                            "Must be UNIQUE in the artifact. If the string appears multiple times, "
                            "include more surrounding context to make it unique."
                        )
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The replacement string. Can be empty to delete the old_str."
                    }
                },
                "required": ["artifact_id", "old_str", "new_str"]
            }
        }
    }


def get_update_artifact_inline_tool_claude() -> Dict:
    """Get update_artifact_inline tool definition in Claude/Anthropic format."""
    openai_spec = get_update_artifact_inline_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"]
    }


# ============ REGISTRY ============

class DareToolRegistry:
    """
    Registry of all available DARE tools.
    
    Maps tool function names to their definitions and executors.
    """
    
    # Registry mapping function_name -> tool config
    TOOLS: Dict[str, Dict] = {
        "create_diagram": {
            "name": "Create Diagram",
            "slug": "create_diagram",
            "description": "Create visual diagrams including flowcharts, sequence diagrams, mindmaps, and more using Mermaid syntax.",
            "icon": "diagram",
            "category": "visualization",
            "get_openai_schema": get_diagram_tool_openai,
            "get_claude_schema": get_diagram_tool_claude,
            "executor": execute_create_diagram,
        },
        "create_chart": {
            "name": "Create Chart",
            "slug": "create_chart",
            "description": "Create data visualization charts including bar, line, pie, and other chart types.",
            "icon": "chart",
            "category": "visualization",
            "get_openai_schema": get_chart_tool_openai,
            "get_claude_schema": get_chart_tool_claude,
            "executor": execute_create_chart,
        },
        "update_artifact": {
            "name": "Update Artifact",
            "slug": "update_artifact",
            "description": "Update an existing artifact (diagram, chart, etc.) by creating a new version with modified content.",
            "icon": "edit",
            "category": "visualization",
            "get_openai_schema": get_update_artifact_tool_openai,
            "get_claude_schema": get_update_artifact_tool_claude,
            "executor": None,  # Handled by ArtifactToolExecutor directly
        },
        "update_artifact_inline": {
            "name": "Update Artifact Inline",
            "slug": "update_artifact_inline",
            "description": "Make targeted string replacements in an existing artifact for small edits.",
            "icon": "edit-inline",
            "category": "visualization",
            "get_openai_schema": get_update_artifact_inline_tool_openai,
            "get_claude_schema": get_update_artifact_inline_tool_claude,
            "executor": None,  # Handled by ArtifactToolExecutor directly
        },
    }
    
    @classmethod
    def get_tool(cls, function_name: str) -> Optional[Dict]:
        """Get a tool configuration by function name."""
        return cls.TOOLS.get(function_name)
    
    @classmethod
    def get_all_tools(cls) -> Dict[str, Dict]:
        """Get all registered tools."""
        return cls.TOOLS.copy()
    
    @classmethod
    def get_tool_slugs(cls) -> List[str]:
        """Get all tool slugs."""
        return list(cls.TOOLS.keys())
    
    @classmethod
    def get_openai_schemas(cls, tool_slugs: Optional[List[str]] = None) -> List[Dict]:
        """
        Get OpenAI-format tool schemas for the specified tools.
        
        Args:
            tool_slugs: List of tool slugs to include. If None, include all.
            
        Returns:
            List of OpenAI tool definitions
        """
        schemas = []
        slugs_to_include = tool_slugs or list(cls.TOOLS.keys())
        
        for slug in slugs_to_include:
            tool = cls.TOOLS.get(slug)
            if tool and "get_openai_schema" in tool:
                schema = tool["get_openai_schema"]()
                if schema:
                    schemas.append(schema)
        
        return schemas
    
    @classmethod
    def get_claude_schemas(cls, tool_slugs: Optional[List[str]] = None) -> List[Dict]:
        """
        Get Claude-format tool schemas for the specified tools.
        
        Args:
            tool_slugs: List of tool slugs to include. If None, include all.
            
        Returns:
            List of Claude tool definitions
        """
        schemas = []
        slugs_to_include = tool_slugs or list(cls.TOOLS.keys())
        
        for slug in slugs_to_include:
            tool = cls.TOOLS.get(slug)
            if tool and "get_claude_schema" in tool:
                schema = tool["get_claude_schema"]()
                if schema:
                    schemas.append(schema)
        
        return schemas
    
    @classmethod
    def execute_tool(cls, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool by function name.
        
        Args:
            function_name: The function name (e.g., 'create_diagram')
            arguments: Arguments to pass to the executor
            
        Returns:
            Execution result dict
        """
        tool = cls.TOOLS.get(function_name)
        if not tool:
            return {
                "success": False,
                "error": f"Unknown tool: {function_name}"
            }
        
        executor = tool.get("executor")
        if not executor:
            return {
                "success": False,
                "error": f"Tool {function_name} has no executor"
            }
        
        return executor(arguments)
    
    @classmethod
    def is_dare_tool(cls, function_name: str) -> bool:
        """Check if a function name is a DARE tool."""
        return function_name in cls.TOOLS


# Convenience function for imports
def get_dare_tool_schemas(tool_slugs: Optional[List[str]] = None, provider: str = "openai") -> List[Dict]:
    """
    Get tool schemas for the specified provider.
    
    Args:
        tool_slugs: List of tool slugs to include. If None, include all.
        provider: LLM provider ('openai', 'claude', etc.)
        
    Returns:
        List of tool definitions in the provider's format
    """
    provider_lower = provider.lower()
    
    if provider_lower in ["openai", "azure_openai"]:
        return DareToolRegistry.get_openai_schemas(tool_slugs)
    elif provider_lower in ["claude", "anthropic"]:
        return DareToolRegistry.get_claude_schemas(tool_slugs)
    else:
        # Default to OpenAI format
        logger.warning(f"Unknown provider {provider}, using OpenAI format")
        return DareToolRegistry.get_openai_schemas(tool_slugs)
