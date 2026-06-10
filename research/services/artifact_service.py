"""
Artifact generation — a structured contract, not prose-scraping.

The Presentation Assistant is asked to return a single JSON object describing the
artifact(s) it produced. We parse that with json.loads (no regex, no markdown
heuristics): the structure is the contract. Chat replies are rendered inline on
the frontend (markdown), but artifacts are *created* only through this path.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Renderable artifact types the FE registry understands.
ALLOWED_TYPES = {"diagram", "html", "svg", "excalidraw", "code", "document"}

_TYPE_BRIEF = {
    "diagram": "a Mermaid diagram (content = the mermaid source)",
    "svg": "an SVG figure (content = raw <svg>…</svg> markup)",
    "html": "a self-contained HTML page (content = the full HTML)",
    "excalidraw": 'an Excalidraw scene (content = the scene JSON string, {"type":"excalidraw","version":2,"elements":[…]})',
    "code": "a code snippet (content = the code)",
    "document": "a written document (content = GitHub-flavored Markdown)",
}


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
        "You are the Presentation Assistant. "
        + want
        + "\n\nReturn ONLY a single JSON object — no prose, no markdown fences — "
        'shaped exactly: {"artifacts": [{"type": "...", "title": "...", '
        '"content": "..."}]}. `content` is the raw artifact payload (mermaid/svg/'
        "html text, or the Excalidraw scene JSON as a string)."
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


def parse_artifacts(output):
    """
    Parse the JSON artifact envelope into a list of
    {artifact_type, title, content}. Returns [] if the output isn't the contract.
    """
    if not output:
        return []
    try:
        data = json.loads(_strip_code_fence(output))
    except json.JSONDecodeError:
        logger.warning("Artifact output was not valid JSON")
        return []

    items = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    artifacts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        atype = str(item.get("type") or "").strip().lower()
        content = item.get("content")
        if (
            atype not in ALLOWED_TYPES
            or not isinstance(content, str)
            or not content.strip()
        ):
            continue
        artifacts.append(
            {
                "artifact_type": atype,
                "title": str(item.get("title") or atype).strip(),
                "content": content,
            }
        )
    return artifacts
