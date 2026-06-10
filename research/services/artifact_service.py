"""
Artifact generation — a structured contract, not prose-scraping.

The Presentation Assistant is asked to return a single JSON object describing the
artifact(s) it produced. We parse that with json.loads (no regex, no markdown
heuristics): the structure is the contract. Chat replies are rendered inline on
the frontend (markdown), but artifacts are *created* only through this path.
"""

import json
import logging

from dare_tools.services.pptx_tool import (
    execute_create_pptx,
    get_create_pptx_tool_openai,
)
from dare_tools.services.registry import (
    execute_create_docx,
    get_create_docx_tool_openai,
)
from research.services.scout_service import find_json_object

logger = logging.getLogger(__name__)

# Renderable artifact types the FE registry understands.
ALLOWED_TYPES = {
    "diagram",
    "html",
    "svg",
    "excalidraw",
    "code",
    "document",
    "docx",
    "pptx",
}

_TYPE_BRIEF = {
    "diagram": "a Mermaid diagram (content = the mermaid source)",
    "svg": "an SVG figure (content = raw <svg>…</svg> markup)",
    "html": "a self-contained HTML page (content = the full HTML)",
    "excalidraw": 'an Excalidraw scene (content = the scene JSON string, {"type":"excalidraw","version":2,"elements":[…]})',
    "code": "a code snippet (content = the code)",
    "document": "a written document (content = GitHub-flavored Markdown)",
    "docx": (
        "a Word document (content = a JSON object "
        '{"title": "...", "blocks": [...]} where each block is one of: '
        '{"type": "heading", "level": 1-4, "text": "..."}, '
        '{"type": "paragraph", "text": "...", "alignment"?: "left|center|right"}, '
        '{"type": "list", "items": ["..."], "ordered"?: true}, '
        '{"type": "table", "headers": ["..."], "rows": [["..."]]}, '
        '{"type": "blockquote", "text": "..."})'
    ),
    "pptx": (
        "a PowerPoint deck (content = a JSON object "
        '{"title": "...", "subtitle"?: "...", "slides": [...]} with 5-10 slides). '
        "Required slide fields BY LAYOUT — title/section: title (+ optional "
        "subtitle, body); bullets/summary: title + bullets (3-5 strings, each "
        "under 120 chars); twoColumn: title + leftBullets + rightBullets "
        "(+ optional leftTitle, rightTitle); table: title + headers + rows; "
        "quote: quote (+ optional attribution). Start with a title slide; every "
        "layout supports speakerNotes — put citations and detail there, not on "
        "the slide"
    ),
}

# Structured types ride the same envelope but their content is a config DARE
# validates with the SAME validators the main chat tools use — one schema,
# no parallel implementation.
_STRUCTURED_VALIDATORS = {
    "docx": (execute_create_docx, "doc_config"),
    "pptx": (execute_create_pptx, "ppt_config"),
}

_STRUCTURED_SCHEMAS = {
    "docx": get_create_docx_tool_openai,
    "pptx": get_create_pptx_tool_openai,
}


def _structured_schema(artifact_type):
    """
    The canonical JSON Schema for a structured type's `content` — the exact
    schema DARE's chat tools advertise, embedded verbatim so the agent isn't
    working from a prose paraphrase of it.
    """
    getter = _STRUCTURED_SCHEMAS.get(artifact_type)
    if not getter:
        return ""
    return json.dumps(getter()["function"]["parameters"])


def build_artifact_instructions(soul_content, artifact_type=""):
    """
    Compose the run instructions: the soul file + a JSON output contract. A blank
    artifact_type lets the agent pick the most fitting renderable type.
    """
    parts = []
    if soul_content and soul_content.strip():
        parts.append("# Research standards (soul file)\n" + soul_content.strip())

    if artifact_type in _TYPE_BRIEF:
        want = f'Produce {_TYPE_BRIEF[artifact_type]}; set "type" to "{artifact_type}".'
    else:
        want = (
            'Produce the most fitting renderable artifact; set "type" to one of '
            "diagram (Mermaid), svg, html, or document (Markdown)."
        )

    parts.append(
        "You are the Presentation Assistant. The scholar describes what they "
        "want in plain language — infer the most fitting structure, content "
        "and emphasis from their words and the project's approved knowledge; "
        "never require specifications or ask follow-up questions. Everything "
        "you produce must be about THIS project (its research question and "
        "approved knowledge). If the request names no subject and the project "
        'context gives you nothing concrete, return {"artifacts": []} — never '
        "invent content unrelated to the project. "
        + want
        + "\n\nReturn ONLY a single JSON object — no prose, no markdown fences — "
        'shaped exactly: {"artifacts": [{"type": "...", "title": "...", '
        '"content": ...}]}. `content` is the raw artifact payload (mermaid/svg/'
        "html/markdown text, the Excalidraw scene JSON as a string, or for "
        "docx/pptx the document/deck JSON object itself)."
    )
    schema = _structured_schema(artifact_type)
    if schema:
        parts.append(
            f"The `content` object MUST conform to this JSON Schema:\n{schema}"
        )
    return "\n\n".join(parts)


def _strip_code_fence(text):
    """Drop a single wrapping ``` fence if the model added one (no regex)."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    newline = text.find("\n")
    if newline != -1:
        text = text[newline + 1 :]
    end = text.rfind("```")
    if end != -1:
        text = text[:end]
    return text.strip()


def parse_artifacts(output, errors=None):
    """
    Parse the JSON artifact envelope into a list of
    {artifact_type, title, content}. Returns [] if the output isn't the contract.
    Pass `errors` (a list) to collect the specific reasons items were rejected —
    the repair re-ask feeds these back so the model fixes the actual problem.
    """
    if errors is None:
        errors = []
    if not output:
        return []
    data = find_json_object(_strip_code_fence(output), required_key="artifacts")
    if not isinstance(data, dict):
        logger.warning("Artifact output was not valid JSON")
        errors.append("the reply was not a single valid JSON object")
        return []

    items = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        errors.append('the JSON object had no "artifacts" array')
        return []

    artifacts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        atype = str(item.get("type") or "").strip().lower()
        content = item.get("content")
        if atype not in ALLOWED_TYPES:
            errors.append(f'"{atype}" is not an allowed artifact type')
            continue

        if atype in _STRUCTURED_VALIDATORS:
            validator, config_key = _STRUCTURED_VALIDATORS[atype]
            if isinstance(content, str):
                try:
                    content = json.loads(_strip_code_fence(content))
                except json.JSONDecodeError:
                    logger.warning("%s artifact content was not valid JSON", atype)
                    errors.append(f"the {atype} content was not valid JSON")
                    continue
            if not isinstance(content, dict):
                errors.append(f"the {atype} content must be a JSON object")
                continue
            result = validator(content)
            if not result.get("success"):
                logger.warning(
                    "%s artifact failed validation: %s", atype, result.get("error")
                )
                errors.append(f"{atype}: {result.get('error')}")
                continue
            content = json.dumps(result[config_key], indent=2)
        elif not isinstance(content, str) or not content.strip():
            errors.append(f"the {atype} content must be a non-empty string")
            continue

        artifacts.append(
            {
                "artifact_type": atype,
                "title": str(item.get("title") or atype).strip(),
                "content": content,
            }
        )
    return artifacts
