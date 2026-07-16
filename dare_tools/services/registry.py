"""
DARE Tools Registry.

Central registry for all internal DARE tools. Maps tool slugs to their
definitions and executors.
"""

import logging
from typing import Dict, List, Optional, Any, Callable

from core.services.llm_utils.diagram_tool import (get_diagram_tool_claude,
                                                  get_diagram_tool_openai,
                                                  json_to_mermaid)
from dare_tools.services.pptx_tool import (execute_create_pptx,
                                           get_create_pptx_tool_claude,
                                           get_create_pptx_tool_openai)

# fmt: on

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


def execute_create_docx(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the create_docx tool.

    Args:
        arguments: Dict with title and blocks

    Returns:
        Dict with validated document configuration
    """
    try:
        title = arguments.get("title", "").strip()
        blocks = arguments.get("blocks", [])

        if not title:
            return {
                "success": False,
                "error": "Document title is required",
            }

        if not isinstance(blocks, list) or not blocks:
            return {
                "success": False,
                "error": "At least one document block is required",
            }

        allowed_alignments = {"left", "center", "right"}
        supported_types = {"heading", "paragraph", "list", "table", "blockquote"}

        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                return {
                    "success": False,
                    "error": f"Block {index + 1} must be an object",
                }

            block_type = block.get("type")
            if block_type not in supported_types:
                return {
                    "success": False,
                    "error": f"Unsupported block type in block {index + 1}: {block_type}",
                }

            if block_type == "heading":
                level = block.get("level")
                text = block.get("text", "").strip()
                if level not in [1, 2, 3, 4] or not text:
                    return {
                        "success": False,
                        "error": f"Heading block {index + 1} requires level 1-4 and text",
                    }

            elif block_type == "paragraph":
                text = block.get("text", "").strip()
                alignment = block.get("alignment", "left")
                if not text:
                    return {
                        "success": False,
                        "error": f"Paragraph block {index + 1} requires text",
                    }
                if alignment not in allowed_alignments:
                    return {
                        "success": False,
                        "error": f"Paragraph block {index + 1} has invalid alignment",
                    }

            elif block_type == "list":
                items = block.get("items", [])
                if not isinstance(items, list) or not items:
                    return {
                        "success": False,
                        "error": f"List block {index + 1} requires items",
                    }

            elif block_type == "blockquote":
                text = block.get("text", "").strip()
                if not text:
                    return {
                        "success": False,
                        "error": f"Blockquote block {index + 1} requires text",
                    }

            elif block_type == "table":
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                if not isinstance(headers, list) or not headers:
                    return {
                        "success": False,
                        "error": f"Table block {index + 1} requires headers",
                    }
                if not isinstance(rows, list):
                    return {
                        "success": False,
                        "error": f"Table block {index + 1} rows must be a list",
                    }
                header_count = len(headers)
                for row_idx, row in enumerate(rows):
                    if not isinstance(row, list) or len(row) != header_count:
                        return {
                            "success": False,
                            "error": f"Table block {index + 1}, row {row_idx + 1} must have {header_count} cells",
                        }

        return {
            "success": True,
            "doc_config": {
                "title": title,
                "blocks": blocks,
            },
        }
    except Exception as e:
        logger.exception(f"Error executing create_docx: {e}")
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


def get_create_docx_tool_openai() -> Dict:
    """Get create_docx tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_docx",
            "description": (
                "Create a NEW structured Word document artifact. "
                "Use this ONLY for creating brand new documents. "
                "If the user wants to MODIFY, UPDATE, or CHANGE an existing document, "
                "you MUST use the update_artifact tool instead with the existing artifact_id "
                "and provide the complete updated JSON document spec as the content. "
                "Use this when the user asks for a document, proposal, report, brief, "
                "plan, summary, or other downloadable structured output. "
                "Structure the document with heading blocks (level 1 for main sections, "
                "level 2 for subsections), paragraphs for body text (2-4 sentences each), "
                "lists for enumerations, tables for tabular data, and blockquotes for "
                "callouts or important notes. Always start with a level 1 heading. "
                "Use multiple blocks to create a well-structured, professional document. "
                "Ensure each table row has the same number of cells as headers. "
                "Do not answer with plain text when a structured document is requested."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Document title displayed at the top of the document.",
                    },
                    "blocks": {
                        "type": "array",
                        "description": (
                            "Ordered list of document blocks. Start with a level 1 heading, "
                            "then use paragraphs, lists, tables, and blockquotes to build "
                            "a complete, well-structured document."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["heading", "paragraph", "list", "table", "blockquote"],
                                },
                                "level": {
                                    "type": "integer",
                                    "enum": [1, 2, 3, 4],
                                    "description": "Heading level (1=main section, 2=subsection, 3-4=sub-subsection). Only for heading blocks.",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Text content. Required for heading, paragraph, and blockquote blocks.",
                                },
                                "alignment": {
                                    "type": "string",
                                    "enum": ["left", "center", "right"],
                                    "description": "Text alignment. Only for paragraph blocks. Defaults to left.",
                                },
                                "ordered": {
                                    "type": "boolean",
                                    "description": "Whether the list is numbered (true) or bulleted (false). Only for list blocks.",
                                },
                                "items": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List items. Only for list blocks.",
                                },
                                "headers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Table column headers. Only for table blocks.",
                                },
                                "rows": {
                                    "type": "array",
                                    "items": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "description": "Table data rows. Each row must have the same number of cells as headers. Only for table blocks.",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["title", "blocks"],
            },
        },
    }


def get_create_docx_tool_claude() -> Dict:
    """Get create_docx tool definition in Claude/Anthropic format."""
    openai_spec = get_create_docx_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"],
    }


def get_update_artifact_tool_openai() -> Dict:
    """Get update_artifact tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "update_artifact",
            "description": (
                "Replace entire artifact content with new content. "
                "USE FOR: "
                "\n• Changing color schemes (cyan → orange affects multiple places)"
                "\n• Restructuring layout or content"
                "\n• Any change affecting multiple locations in the code"
                "\n• Major rewrites (>30% of content)"
                "\n\n"
                "This is PREFERRED over multiple update_artifact_inline calls. "
                "Always provide the COMPLETE new content - partial updates will break the artifact. "
                "You MUST reference the artifact_id of the artifact to update. "
                "For React components, provide the full code. "
                "For diagrams, provide the complete Mermaid code. "
                "For docx documents, provide the complete JSON document spec "
                "with title and blocks array (same format as create_docx). "
                "For pptx presentations, provide the complete JSON presentation "
                "spec with title, theme, and slides array (same format as create_pptx)."
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
                        "description": "The COMPLETE new content for the artifact. For React components, provide the full code. For diagrams, provide the complete Mermaid code. For docx documents, provide the complete JSON string with {\"title\": \"...\", \"blocks\": [...]} structure."
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
                "Make a SINGLE targeted string replacement in an artifact. "
                "USE FOR: ONE small edit like fixing a typo, changing one value, or updating one small section. "
                "\n\n"
                "⚠️ IMPORTANT RULES:"
                "\n• NEVER call this tool multiple times in parallel - each call creates a new version!"
                "\n• If you need to make MORE THAN ONE change (e.g., changing a color scheme from cyan to orange), "
                "use update_artifact with the FULL new code instead."
                "\n• old_str must be UNIQUE in the artifact. If it appears multiple times, include more context."
                "\n\n"
                "WHEN TO USE update_artifact INSTEAD:"
                "\n• Changing color schemes (multiple classes need updating)"
                "\n• Restructuring content"
                "\n• Updating multiple values"
                "\n• Any change affecting >1 location in the code"
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


def get_create_react_component_tool_openai() -> Dict:
    """Get create_react_component tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_react_component",
            "description": (
                "Create an interactive React component rendered in the artifact panel. "
                "Use for: interactive UIs, forms, todo lists, calculators, games, widgets, dashboards."
                "\n\n"
                "✅ USE STANDARD ES6 IMPORTS - The component runs in a full React sandbox with npm support."
                "\n\n"
                "AVAILABLE PACKAGES (import what you need):"
                "\n• React: import { useState, useEffect, useRef, useMemo, useCallback } from 'react'"
                "\n• Icons: import { Heart, Star, Settings, ... } from 'lucide-react' (ALL 1400+ icons available!)"
                "\n• Tailwind CSS is pre-loaded - just use className"
                "\n\n"
                "STYLING - MODERN SHADCN PATTERNS:"
                "\n• Use Tailwind CSS classes. NO inline styles."
                "\n• MINIMAL AND CLEAN - avoid excessive gradients and decorative elements"
                "\n"
                "\n🎨 THEME COLORS (use true black/white, not gray):"
                "\n  DARK THEME (default): bg-black or bg-neutral-950 for backgrounds, text-white"
                "\n  Cards/containers: bg-neutral-900 border-neutral-800"
                "\n  LIGHT THEME: bg-white text-black, Cards: bg-neutral-50 border-neutral-200"
                "\n"
                "\n🎨 ACCENT COLORS - Pick ONE randomly from these for variety:"
                "\n  • Blue: text-blue-500, bg-blue-500, border-blue-500 (trustworthy, tech)"
                "\n  • Green: text-emerald-500, bg-emerald-500 (success, growth)"
                "\n  • Cyan: text-cyan-500, bg-cyan-500 (fresh, modern)"
                "\n  • Orange: text-orange-500, bg-orange-500 (energy, action)"
                "\n  • Red: text-red-500, bg-red-500 (alerts, important)"
                "\n  • Amber: text-amber-500, bg-amber-500 (warm, attention)"
                "\n  ⚠️ AVOID purple - it's overused"
                "\n"
                "\n💡 UI PATTERNS:"
                "\n  • Button hover: hover:bg-{color}-600"
                "\n  • Subtle backgrounds: bg-{color}-500/10 (10% opacity)"
                "\n  • Borders: border-{color}-500/20 or border-neutral-800"
                "\n  • Focus rings: focus:ring-2 focus:ring-{color}-500"
                "\n  • Cards: rounded-xl shadow-lg p-6"
                "\n\n"
                "EXAMPLE:"
                "\n```jsx"
                "\nimport { useState } from 'react'"
                "\nimport { Zap, Plus, RefreshCw } from 'lucide-react'"
                "\n"
                "\nexport default function App() {"
                "\n  const [count, setCount] = useState(0)"
                "\n  return ("
                "\n    <div className=\"min-h-screen bg-black p-8 text-white\">"
                "\n      <div className=\"max-w-md mx-auto bg-neutral-900 rounded-xl border border-neutral-800 p-6\">"
                "\n        <h1 className=\"flex items-center gap-2 text-xl font-bold mb-4\">"
                "\n          <Zap className=\"w-5 h-5 text-cyan-500\" />"
                "\n          Counter"
                "\n        </h1>"
                "\n        <p className=\"text-5xl font-bold text-center text-cyan-500 mb-6\">{count}</p>"
                "\n        <div className=\"flex gap-2\">"
                "\n          <button"
                "\n            onClick={() => setCount(c => c + 1)}"
                "\n            className=\"flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-cyan-500 hover:bg-cyan-600 rounded-lg font-medium transition-colors\""
                "\n          >"
                "\n            <Plus className=\"w-4 h-4\" /> Increment"
                "\n          </button>"
                "\n          <button"
                "\n            onClick={() => setCount(0)}"
                "\n            className=\"flex-1 flex items-center justify-center gap-2 px-4 py-2 border border-neutral-700 hover:bg-neutral-800 rounded-lg font-medium transition-colors\""
                "\n          >"
                "\n            <RefreshCw className=\"w-4 h-4\" /> Reset"
                "\n          </button>"
                "\n        </div>"
                "\n      </div>"
                "\n    </div>"
                "\n  )"
                "\n}"
                "\n```"
                "\n\n"
                "RULES:"
                "\n• USE import statements for React hooks and Lucide icons"
                "\n• Start with 'export default function App()'"
                "\n• No fetch/API calls (blocked)"
                "\n• No localStorage/sessionStorage"
                "\n• For charts/data visualization, use the create_chart tool instead"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for the component"
                    },
                    "code": {
                        "type": "string",
                        "description": "React component code with ES6 imports. Use modern Shadcn patterns: bg-black/bg-white themes, pick random accent color (blue/green/cyan/orange - avoid purple)."
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description (optional)"
                    }
                },
                "required": ["title", "code"]
            }
        }
    }


def get_create_react_component_tool_claude() -> Dict:
    """Get create_react_component tool definition in Claude/Anthropic format."""
    openai_spec = get_create_react_component_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"]
    }


def execute_create_react_component(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute the create_react_component tool.

    Note: The actual artifact creation is handled by ArtifactToolExecutor.
    This function is kept for registry consistency but returns the validated arguments.

    Args:
        arguments: Dict with title, code, description

    Returns:
        Dict with validated component data
    """
    try:
        title = arguments.get("title", "React Component")
        code = arguments.get("code", "")
        description = arguments.get("description", "")

        if not code.strip():
            return {
                "success": False,
                "error": "Component code is required",
            }

        return {
            "success": True,
            "title": title,
            "code": code,
            "description": description,
        }
    except Exception as e:
        logger.exception(f"Error executing create_react_component: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ============ REGISTRY ============

def get_search_documents_tool_openai() -> Dict:
    """Get search_documents tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the user's attached documents and shared libraries for "
                "passages relevant to a query. Use this whenever the answer may "
                "depend on the attached sources — facts, quotes, definitions, "
                "data, or anything you are not certain about. Write a focused "
                "search query describing the information you need (not the "
                "user's message verbatim). Results are passages tagged [S1], "
                "[S2], ...; when you use a passage in your answer, cite it "
                "inline with its [S#] tag. If the question is general knowledge "
                "unrelated to the attached documents, answer directly without "
                "calling this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Focused natural-language search query describing the information needed."
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Number of passages to retrieve. Omit to use the conversation's default."
                    }
                },
                "required": ["query"]
            }
        }
    }


def get_search_documents_tool_claude() -> Dict:
    """Get search_documents tool definition in Claude/Anthropic format."""
    openai_spec = get_search_documents_tool_openai()
    func = openai_spec["function"]
    return {
        "name": func["name"],
        "description": func["description"],
        "input_schema": func["parameters"]
    }


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
        "create_docx": {
            "name": "Create Docx",
            "slug": "create_docx",
            "description": "Create structured Word-style documents with headings, paragraphs, lists, and tables.",
            "icon": "file-text",
            "category": "visualization",
            "get_openai_schema": get_create_docx_tool_openai,
            "get_claude_schema": get_create_docx_tool_claude,
            "executor": execute_create_docx,
        },
        "create_pptx": {
            "name": "Create PPTX",
            "slug": "create_pptx",
            "description": "Create styled PowerPoint presentations with structured slide layouts.",
            "icon": "presentation",
            "category": "visualization",
            "get_openai_schema": get_create_pptx_tool_openai,
            "get_claude_schema": get_create_pptx_tool_claude,
            "executor": execute_create_pptx,
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
        "create_react_component": {
            "name": "Create React Component",
            "slug": "create_react_component",
            "description": "Create interactive React components with Tailwind CSS and Shadcn UI.",
            "icon": "code",
            "category": "visualization",
            "get_openai_schema": get_create_react_component_tool_openai,
            "get_claude_schema": get_create_react_component_tool_claude,
            "executor": execute_create_react_component,
        },
        "search_documents": {
            "name": "Search Documents",
            "slug": "search_documents",
            "description": "Search attached documents and shared libraries for relevant passages (agentic RAG).",
            "icon": "search",
            "category": "retrieval",
            "get_openai_schema": get_search_documents_tool_openai,
            "get_claude_schema": get_search_documents_tool_claude,
            "executor": None,  # Handled by RetrievalToolExecutor directly (async, needs request scope)
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
