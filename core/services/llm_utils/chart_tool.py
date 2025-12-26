"""
Chart Tool Definition

Provides the create_chart tool definition for LLM function calling,
generating structured JSON output compatible with recharts.
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


# ============ DEFAULT COLORS ============

DEFAULT_CHART_COLORS = [
    "#8884d8",  # Purple
    "#82ca9d",  # Green
    "#ffc658",  # Yellow
    "#ff7c43",  # Orange
    "#a4de6c",  # Light green
    "#d0ed57",  # Lime
    "#83a6ed",  # Light blue
    "#8dd1e1",  # Cyan
]


# ============ TOOL DEFINITIONS ============

def get_chart_tool_openai() -> Dict:
    """Get chart tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_chart",
            "description": "Create a data visualization chart. Use this when the user asks for a bar chart, line chart, pie chart, area chart, or any data visualization with numerical data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "area"],
                        "description": "Type of chart to create: bar (bar chart), line (line graph), pie (pie chart), area (area chart)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the chart"
                    },
                    "data": {
                        "type": "array",
                        "description": "Array of data points. Each object should have a name/label key and one or more numeric value keys.",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                            "description": "Data point with name and numeric values, e.g., {\"name\": \"Jan\", \"sales\": 4000, \"profit\": 2400}"
                        }
                    },
                    "dataKeys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Which keys from the data objects to plot as series (e.g., [\"sales\", \"profit\"])"
                    },
                    "xAxisKey": {
                        "type": "string",
                        "description": "The key in data objects to use for X-axis labels (e.g., \"name\" or \"month\")"
                    },
                    "colors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional array of hex color codes for each data series (e.g., [\"#8884d8\", \"#82ca9d\"])"
                    },
                    "xAxisLabel": {
                        "type": "string",
                        "description": "Optional label for the X-axis"
                    },
                    "yAxisLabel": {
                        "type": "string",
                        "description": "Optional label for the Y-axis"
                    }
                },
                "required": ["chart_type", "title", "data", "dataKeys", "xAxisKey"]
            }
        }
    }


def get_chart_tool_claude() -> Dict:
    """Get chart tool definition in Claude/Anthropic format."""
    openai_spec = get_chart_tool_openai()
    return {
        "name": openai_spec["function"]["name"],
        "description": openai_spec["function"]["description"],
        "input_schema": openai_spec["function"]["parameters"]
    }


def get_chart_tool_gemini():
    """Get chart tool definition in Gemini format."""
    from google.genai import types

    openai_spec = get_chart_tool_openai()
    func = openai_spec["function"]

    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=func["name"],
                description=func["description"],
                parameters=func["parameters"]
            )
        ]
    )


def get_chart_tool(provider: str) -> Any:
    """Get chart tool in the format required by the specified provider."""
    provider_lower = provider.lower()

    if provider_lower in ["openai", "azure_openai"]:
        return get_chart_tool_openai()
    elif provider_lower in ["claude", "anthropic"]:
        return get_chart_tool_claude()
    elif provider_lower in ["gemini", "google"]:
        return get_chart_tool_gemini()
    else:
        logger.warning(f"Unknown provider {provider}, using OpenAI format for chart tool")
        return get_chart_tool_openai()


# ============ UTILITY FUNCTIONS ============

def is_chart_request(message: str) -> bool:
    """
    Check if a message is likely requesting a chart/data visualization.
    Used as a heuristic for intent detection.
    """
    message_lower = message.lower()

    chart_keywords = [
        "bar chart", "line chart", "pie chart", "area chart",
        "chart", "graph", "plot", "visualize data", "visualization",
        "data chart", "histogram", "show me a chart",
        "create a chart", "make a chart", "draw a chart",
        "sales chart", "revenue chart", "comparison chart"
    ]

    # Exclude diagram-related requests (handled by diagram_tool)
    diagram_keywords = [
        "diagram", "flowchart", "sequence", "mindmap",
        "state diagram", "class diagram", "architecture"
    ]

    has_chart_keyword = any(keyword in message_lower for keyword in chart_keywords)
    has_diagram_keyword = any(keyword in message_lower for keyword in diagram_keywords)

    # Return True only if it has chart keywords but NOT diagram keywords
    return has_chart_keyword and not has_diagram_keyword


def validate_chart_config(config: Dict) -> bool:
    """
    Validate that the chart configuration has all required fields.

    Args:
        config: The chart configuration from LLM tool call

    Returns:
        True if valid, False otherwise
    """
    required_fields = ["chart_type", "title", "data", "dataKeys", "xAxisKey"]

    for field in required_fields:
        if field not in config:
            logger.warning(f"Chart config missing required field: {field}")
            return False

    if not isinstance(config.get("data"), list) or len(config["data"]) == 0:
        logger.warning("Chart config has empty or invalid data array")
        return False

    if not isinstance(config.get("dataKeys"), list) or len(config["dataKeys"]) == 0:
        logger.warning("Chart config has empty or invalid dataKeys array")
        return False

    valid_types = ["bar", "line", "pie", "area"]
    if config.get("chart_type") not in valid_types:
        logger.warning(f"Chart config has invalid chart_type: {config.get('chart_type')}")
        return False

    return True


def add_default_colors(config: Dict) -> Dict:
    """
    Add default colors to chart config if not provided.

    Args:
        config: The chart configuration

    Returns:
        Config with colors added
    """
    if "colors" not in config or not config["colors"]:
        num_series = len(config.get("dataKeys", []))
        config["colors"] = DEFAULT_CHART_COLORS[:num_series]

    return config
