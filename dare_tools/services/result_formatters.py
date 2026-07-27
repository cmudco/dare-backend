"""
Model-facing text formatting for DARE tool results.

The raw executor result dicts are persisted and sent to the FE as-is;
this module renders the compact text the MODEL sees in its
``role:"tool"`` result turn.
"""

import json
from typing import Any, Dict


def format_dare_result_for_llm(tool_name: str, result: Dict[str, Any]) -> str:
    """Format a DARE tool result as text for the model's tool-result turn."""
    if not result.get("success"):
        return f"Error: {result.get('error', 'Unknown error')}"

    if tool_name == "create_diagram":
        return (
            f"Diagram created successfully. Artifact ID: {result.get('artifact_id')}. "
            f"Mermaid code:\n```mermaid\n{result.get('mermaid_code', '')}\n```"
        )
    elif tool_name == "create_chart":
        chart_config = result.get("chart_config", {})
        return (
            f"Chart created successfully. Artifact ID: {result.get('artifact_id')}. "
            f"Type: {chart_config.get('type')}, Title: {chart_config.get('title')}"
        )
    elif tool_name == "create_docx":
        doc_config = result.get("doc_config", {})
        return (
            f"Document created successfully. Artifact ID: {result.get('artifact_id')}. "
            f"Title: {doc_config.get('title')}, "
            f"Blocks: {len(doc_config.get('blocks', []))}"
        )
    elif tool_name == "create_pptx":
        ppt_config = result.get("ppt_config", {})
        return (
            f"PowerPoint created successfully. Artifact ID: {result.get('artifact_id')}. "
            f"Title: {ppt_config.get('title')}, "
            f"Slides: {len(ppt_config.get('slides', []))}"
        )
    elif tool_name == "search_documents":
        blocks = result.get("blocks") or []
        query = result.get("query", "")
        if not blocks:
            return (
                f'No relevant passages found for query "{query}". '
                "The attached sources may not cover this topic."
            )
        return (
            f'Retrieved {len(blocks)} passages for query "{query}". '
            "When you use a passage in your answer, cite it inline with "
            "its [S#] tag:\n\n" + "\n\n".join(blocks)
        )
    elif tool_name == "create_react_component":
        return f"React component created successfully. Artifact ID: {result.get('artifact_id')}, Title: {result.get('message', 'Component')}"
    elif tool_name == "update_artifact_inline":
        change = result.get("change_summary", {})
        return (
            f"Artifact updated successfully (v{result.get('version')}). "
            f"Replaced {change.get('removed_chars', 0)} chars with {change.get('added_chars', 0)} chars."
        )
    else:
        return json.dumps(result)
