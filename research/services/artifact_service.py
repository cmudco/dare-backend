"""
Artifact generation — a structured contract, not prose-scraping.

The Presentation Assistant returns a short metadata JSON line, a ``===CONTENT===``
marker, then the raw artifact content — so it never hand-escapes a large HTML/
SVG/Markdown blob into a JSON string (which silently lost multi-line artifacts to
escaping errors). Metadata parses reliably; content is taken verbatim. Structured
types (docx/pptx/excalidraw) carry a JSON object as their content, validated with
the same validators the chat tools use. The legacy ``{"artifacts": [...]}``
envelope is still accepted as a fallback. Chat replies render inline on the
frontend (markdown); artifacts are *created* only through this path.
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
        "approved knowledge). Decline ONLY when the request names no subject "
        "at all AND the project context is empty (e.g. just 'generate an "
        "artifact"
        ' in a blank project) — then return {"artifacts": []}. '
        "Any request that names a subject, audience, or purpose (a briefing, "
        "a deck for a team, a diagram of the evidence) MUST produce an "
        "artifact grounded in the project. "
        + want
        + "\n\nReturn EXACTLY this and nothing else — no prose, no markdown "
        "fences: one line of metadata JSON, then a line containing only "
        "===CONTENT===, then the raw artifact content:\n"
        '{"type": "<type>", "title": "<short title>"}\n'
        "===CONTENT===\n"
        "<the content here, verbatim>\n\n"
        "Put the content verbatim after the marker — do NOT wrap it in JSON or "
        "escape it. For diagram/svg/html/document/code the content is the raw "
        "text (Mermaid source, <svg>…</svg>, full HTML, GitHub-flavored "
        "Markdown, code). For excalidraw/docx/pptx the content is the JSON "
        "object itself."
    )
    parts.append(
        "TOOLS: when you search or read the web to ground the artifact, use "
        "mcp_dare_web_search and mcp_dare_fetch_page only — DARE's own audited "
        "web search and reader. Do NOT use any runtime-native web_search, "
        "web_extract, or browser tool. Non-web tools with no DARE equivalent "
        "(e.g. vision) may be used normally."
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


CONTENT_MARKER = "===CONTENT==="


def _build_artifact(atype, title, content, errors):
    """Validate one artifact's type + content, returning the persisted dict, or
    None (appending the specific reason to `errors`). Structured types are
    validated with the same validators the chat tools use."""
    atype = str(atype or "").strip().lower()
    if atype not in ALLOWED_TYPES:
        errors.append(f'"{atype}" is not an allowed artifact type')
        return None

    if atype in _STRUCTURED_VALIDATORS:
        validator, config_key = _STRUCTURED_VALIDATORS[atype]
        if isinstance(content, str):
            try:
                content = json.loads(_strip_code_fence(content))
            except json.JSONDecodeError:
                logger.warning("%s artifact content was not valid JSON", atype)
                errors.append(f"the {atype} content was not valid JSON")
                return None
        if not isinstance(content, dict):
            errors.append(f"the {atype} content must be a JSON object")
            return None
        result = validator(content)
        if not result.get("success"):
            logger.warning(
                "%s artifact failed validation: %s", atype, result.get("error")
            )
            errors.append(f"{atype}: {result.get('error')}")
            return None
        content = json.dumps(result[config_key], indent=2)
    elif not isinstance(content, str) or not content.strip():
        errors.append(f"the {atype} content must be a non-empty string")
        return None

    return {
        "artifact_type": atype,
        "title": str(title or atype).strip(),
        "content": content,
    }


def parse_artifacts(output, errors=None):
    """
    Parse the artifact contract into a list of {artifact_type, title, content}.

    Primary contract: a short metadata JSON line, then a ``===CONTENT===``
    marker, then the raw artifact content — so the model never hand-escapes a
    large HTML/SVG/Markdown blob into a JSON string (the escaping failures that
    silently lost multi-line artifacts). Falls back to the legacy
    ``{"artifacts": [...]}`` JSON envelope when the model still returns that.
    `errors` (a list) collects the specific rejection reasons.
    """
    if errors is None:
        errors = []
    if not output:
        return []

    # Primary: metadata line + ===CONTENT=== + raw content (one artifact).
    if CONTENT_MARKER in output:
        head, _, content = output.partition(CONTENT_MARKER)
        meta = find_json_object(head, required_key="type")
        if isinstance(meta, dict) and content.strip():
            built = _build_artifact(
                meta.get("type"), meta.get("title"), content.strip(), errors
            )
            return [built] if built else []

    # Fallback: the legacy JSON envelope (works when the model escaped correctly).
    data = find_json_object(_strip_code_fence(output), required_key="artifacts")
    if not isinstance(data, dict):
        logger.warning("Artifact output matched neither the marker nor JSON contract")
        errors.append("the reply was not the artifact contract")
        return []
    items = data.get("artifacts")
    if not isinstance(items, list):
        errors.append('the reply had no "artifacts" array')
        return []

    artifacts = []
    for item in items:
        if isinstance(item, dict):
            built = _build_artifact(
                item.get("type"), item.get("title"), item.get("content"), errors
            )
            if built:
                artifacts.append(built)
    return artifacts
