"""
Diagram Tool Definition and Converter

Provides the create_diagram tool definition for LLM function calling,
and utilities to convert structured JSON output to mermaid syntax.
"""

import re
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ============ TOOL DEFINITIONS ============

def get_diagram_tool_openai() -> Dict:
    """Get diagram tool definition in OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": "create_diagram",
            "description": "Create a visual diagram or flowchart. Use this when the user asks for a diagram, flowchart, sequence diagram, mindmap, or any visual representation of a process, workflow, or system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "enum": ["flowchart", "sequence", "mindmap", "pie", "state", "class"],
                        "description": "Type of diagram to create"
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the diagram"
                    },
                    "nodes": {
                        "type": "array",
                        "description": "List of nodes/elements in the diagram",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique identifier - use simple alphanumeric like 'step1', 'login', 'validate'. Avoid reserved words like 'end', 'graph', 'style'."},
                                "label": {"type": "string", "description": "Display text - keep simple, no parentheses (), brackets [], or special characters"},
                                "shape": {
                                    "type": "string",
                                    "enum": ["box", "circle", "diamond", "stadium", "cylinder", "hexagon"],
                                    "description": "Node shape: box (rectangle), circle, diamond (decision), stadium (rounded/pill), cylinder (database), hexagon"
                                }
                            },
                            "required": ["id", "label"]
                        }
                    },
                    "edges": {
                        "type": "array",
                        "description": "List of connections between nodes",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string", "description": "Source node ID"},
                                "to": {"type": "string", "description": "Target node ID"},
                                "label": {"type": "string", "description": "Optional SHORT label - use simple text like 'Yes', 'No', 'Valid'. NO parentheses or special characters."}
                            },
                            "required": ["from", "to"]
                        }
                    }
                },
                "required": ["diagram_type", "title", "nodes", "edges"]
            }
        }
    }


def get_diagram_tool_claude() -> Dict:
    """Get diagram tool definition in Claude/Anthropic format."""
    return {
        "name": "create_diagram",
        "description": "Create a visual diagram or flowchart. Use this when the user asks for a diagram, flowchart, sequence diagram, mindmap, or any visual representation of a process, workflow, or system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "diagram_type": {
                    "type": "string",
                    "enum": ["flowchart", "sequence", "mindmap", "pie", "state", "class"],
                    "description": "Type of diagram to create"
                },
                "title": {
                    "type": "string",
                    "description": "Title of the diagram"
                },
                "nodes": {
                    "type": "array",
                    "description": "List of nodes/elements in the diagram",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique identifier - simple alphanumeric. Avoid 'end', 'graph', 'style'"},
                            "label": {"type": "string", "description": "Display text - no parentheses or brackets"},
                            "shape": {"type": "string", "enum": ["box", "circle", "diamond", "stadium", "cylinder", "hexagon"], "description": "Node shape"}
                        },
                        "required": ["id", "label"]
                    }
                },
                "edges": {
                    "type": "array",
                    "description": "List of connections between nodes",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "label": {"type": "string", "description": "Short label - no special characters"}
                        },
                        "required": ["from", "to"]
                    }
                }
            },
            "required": ["diagram_type", "title", "nodes", "edges"]
        }
    }


def get_diagram_tool_gemini():
    """Get diagram tool definition in Gemini format."""
    from google.genai import types
    
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="create_diagram",
                description="Create a visual diagram or flowchart",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "diagram_type": types.Schema(
                            type=types.Type.STRING,
                            enum=["flowchart", "sequence", "mindmap", "pie", "state", "class"]
                        ),
                        "title": types.Schema(type=types.Type.STRING),
                        "nodes": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "id": types.Schema(type=types.Type.STRING),
                                    "label": types.Schema(type=types.Type.STRING),
                                    "shape": types.Schema(type=types.Type.STRING)
                                },
                                required=["id", "label"]
                            )
                        ),
                        "edges": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "from": types.Schema(type=types.Type.STRING),
                                    "to": types.Schema(type=types.Type.STRING),
                                    "label": types.Schema(type=types.Type.STRING)
                                },
                                required=["from", "to"]
                            )
                        )
                    },
                    required=["diagram_type", "title", "nodes", "edges"]
                )
            )
        ]
    )


def get_diagram_tool(provider: str) -> Any:
    """Get diagram tool in the format required by the specified provider."""
    provider_lower = provider.lower()
    
    if provider_lower in ["openai", "azure_openai"]:
        return get_diagram_tool_openai()
    elif provider_lower in ["claude", "anthropic"]:
        return get_diagram_tool_claude()
    elif provider_lower in ["gemini", "google"]:
        return get_diagram_tool_gemini()
    else:
        # Default to OpenAI format for unknown providers
        logger.warning(f"Unknown provider {provider}, using OpenAI format for diagram tool")
        return get_diagram_tool_openai()


# Mermaid reserved keywords that cannot be used as node IDs
MERMAID_RESERVED_KEYWORDS = {
    'end', 'graph', 'subgraph', 'direction', 'click', 'style', 'classDef',
    'class', 'linkStyle', 'callback', 'note', 'participant', 'actor',
    'loop', 'alt', 'else', 'opt', 'par', 'and', 'rect', 'state'
}


def _sanitize_node_id(node_id: str) -> str:
    """Sanitize node ID for mermaid compatibility."""
    # Replace special characters with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9]', '_', str(node_id))
    # Ensure it starts with a letter (mermaid requirement)
    if sanitized and sanitized[0].isdigit():
        sanitized = 'n' + sanitized
    # Handle empty result
    if not sanitized:
        sanitized = 'node'
    # Prefix reserved keywords to avoid mermaid syntax errors
    if sanitized.lower() in MERMAID_RESERVED_KEYWORDS:
        sanitized = 'node_' + sanitized
    return sanitized


def _escape_label(label: str) -> str:
    """Escape label text for mermaid."""
    # Replace quotes and special characters
    return str(label).replace('"', "'").replace('\n', ' ')


def json_to_mermaid(data: Dict) -> str:
    """
    Convert structured JSON diagram data to valid mermaid syntax.
    
    Args:
        data: Dictionary with diagram_type, title, nodes, edges
        
    Returns:
        Valid mermaid syntax string
    """
    diagram_type = data.get("diagram_type", "flowchart").lower()
    title = data.get("title", "Diagram")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    
    logger.debug(f"Converting {diagram_type} diagram with {len(nodes)} nodes and {len(edges)} edges")
    
    if diagram_type == "flowchart":
        return _build_flowchart(title, nodes, edges)
    elif diagram_type == "sequence":
        return _build_sequence_diagram(title, nodes, edges)
    elif diagram_type == "mindmap":
        return _build_mindmap(title, nodes, edges)
    elif diagram_type == "pie":
        return _build_pie_chart(title, nodes)
    elif diagram_type == "state":
        return _build_state_diagram(title, nodes, edges)
    elif diagram_type == "class":
        return _build_class_diagram(title, nodes, edges)
    else:
        # Default to flowchart
        logger.warning(f"Unknown diagram type '{diagram_type}', defaulting to flowchart")
        return _build_flowchart(title, nodes, edges)


def _build_flowchart(title: str, nodes: List[Dict], edges: List[Dict]) -> str:
    """Build a flowchart diagram."""
    lines = ["flowchart TD"]
    
    # Add comment with title
    lines.append(f"    %% {title}")
    
    # Add nodes with OFFICIAL mermaid v10.x syntax
    # Reference: https://mermaid.js.org/syntax/flowchart.html#node-shapes
    for node in nodes:
        node_id = _sanitize_node_id(node.get("id", ""))
        label = _escape_label(node.get("label", node_id))
        shape = node.get("shape", "box")
        
        # Official Mermaid v10.x shape syntax (text without outer quotes):
        # Rectangle/box:     id[text]
        # Round edges:       id(text)  
        # Stadium (pill):    id([text])
        # Cylinder (db):     id[(text)]
        # Circle:            id((text))
        # Rhombus/diamond:   id{text}
        # Hexagon:           id{{text}}
        if shape == "diamond":
            lines.append(f'    {node_id}{{{label}}}')
        elif shape == "circle":
            lines.append(f'    {node_id}(({label}))')
        elif shape == "stadium":
            lines.append(f'    {node_id}([{label}])')
        elif shape == "cylinder":
            lines.append(f'    {node_id}[({label})]')
        elif shape == "hexagon":
            lines.append(f'    {node_id}{{{{{label}}}}}')
        else:  # box (default)
            lines.append(f'    {node_id}[{label}]')
    
    # Add edges
    for edge in edges:
        from_id = _sanitize_node_id(edge.get("from", ""))
        to_id = _sanitize_node_id(edge.get("to", ""))
        label = edge.get("label", "")
        
        if from_id and to_id:
            if label:
                lines.append(f'    {from_id}-->|{_escape_label(label)}|{to_id}')
            else:
                lines.append(f'    {from_id}-->{to_id}')
    
    return "\n".join(lines)


def _build_sequence_diagram(title: str, nodes: List[Dict], edges: List[Dict]) -> str:
    """Build a sequence diagram."""
    lines = ["sequenceDiagram"]
    lines.append(f"    %% {title}")
    
    # Add participants
    for node in nodes:
        node_id = _sanitize_node_id(node.get("id", ""))
        label = _escape_label(node.get("label", node_id))
        lines.append(f'    participant {node_id} as {label}')
    
    # Add interactions
    for edge in edges:
        from_id = _sanitize_node_id(edge.get("from", ""))
        to_id = _sanitize_node_id(edge.get("to", ""))
        label = edge.get("label", "")
        
        if from_id and to_id:
            lines.append(f'    {from_id}->>{to_id}: {_escape_label(label)}')
    
    return "\n".join(lines)


def _build_mindmap(title: str, nodes: List[Dict], edges: List[Dict]) -> str:
    """Build a mindmap diagram."""
    lines = ["mindmap"]
    lines.append(f"  root(({_escape_label(title)}))")
    
    # Build tree structure from edges
    # For simplicity, just add nodes as direct children
    for node in nodes:
        label = _escape_label(node.get("label", ""))
        lines.append(f"    {label}")
    
    return "\n".join(lines)


def _build_pie_chart(title: str, nodes: List[Dict]) -> str:
    """Build a pie chart."""
    lines = ["pie showData"]
    lines.append(f'    title {_escape_label(title)}')
    
    for node in nodes:
        label = _escape_label(node.get("label", ""))
        # Use 'value' if present, otherwise default to 10
        value = node.get("value", 10)
        lines.append(f'    "{label}" : {value}')
    
    return "\n".join(lines)


def _build_state_diagram(title: str, nodes: List[Dict], edges: List[Dict]) -> str:
    """Build a state diagram."""
    lines = ["stateDiagram-v2"]
    lines.append(f"    %% {title}")
    
    # Add transitions
    for edge in edges:
        from_id = _sanitize_node_id(edge.get("from", ""))
        to_id = _sanitize_node_id(edge.get("to", ""))
        label = edge.get("label", "")
        
        if from_id and to_id:
            if label:
                lines.append(f'    {from_id} --> {to_id}: {_escape_label(label)}')
            else:
                lines.append(f'    {from_id} --> {to_id}')
    
    return "\n".join(lines)


def _build_class_diagram(title: str, nodes: List[Dict], edges: List[Dict]) -> str:
    """Build a class diagram."""
    lines = ["classDiagram"]
    lines.append(f"    %% {title}")
    
    # Add classes
    for node in nodes:
        node_id = _sanitize_node_id(node.get("id", ""))
        label = _escape_label(node.get("label", node_id))
        lines.append(f'    class {node_id}')
    
    # Add relationships
    for edge in edges:
        from_id = _sanitize_node_id(edge.get("from", ""))
        to_id = _sanitize_node_id(edge.get("to", ""))
        label = edge.get("label", "")
        
        if from_id and to_id:
            lines.append(f'    {from_id} --> {to_id} : {_escape_label(label)}')
    
    return "\n".join(lines)


# ============ UTILITY FUNCTIONS ============

def is_diagram_request(message: str) -> bool:
    """
    Check if a message is likely requesting a diagram.
    Used as a heuristic fallback.
    """
    message_lower = message.lower()
    
    diagram_keywords = [
        "diagram", "flowchart", "flow chart", "sequence",
        "mindmap", "mind map", "chart", "visualize",
        "draw", "sketch", "illustrate", "graph",
        "pie chart", "state diagram", "class diagram",
        "workflow", "process flow", "architecture"
    ]
    
    return any(keyword in message_lower for keyword in diagram_keywords)


def parse_tool_call_response(response: Dict, provider: str) -> Optional[Dict]:
    """
    Parse tool call response from different providers.
    
    Returns:
        Dictionary with parsed arguments, or None if no tool call found
    """
    provider_lower = provider.lower()
    
    try:
        if provider_lower in ["openai", "azure_openai"]:
            # OpenAI format: response.choices[0].message.tool_calls
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                return json.loads(tool_calls[0].get("function", {}).get("arguments", "{}"))
        
        elif provider_lower in ["claude", "anthropic"]:
            # Claude format: content blocks with type "tool_use"
            content = response.get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    return block.get("input", {})
        
        elif provider_lower in ["gemini", "google"]:
            # Gemini format: parts with function_call
            parts = response.get("parts", [])
            for part in parts:
                if "function_call" in part:
                    return dict(part["function_call"].get("args", {}))
        
        return None
        
    except Exception as e:
        logger.error(f"Error parsing tool call response: {e}")
        return None
