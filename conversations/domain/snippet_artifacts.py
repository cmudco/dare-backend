"""Decide how a chat code block becomes an artifact."""

import re
from dataclasses import dataclass

from conversations.constants import (
    ARTIFACT_CONTENT_TYPES,
    SNIPPET_ARTIFACT_LANGUAGES,
    ArtifactType,
)

_REACT_IMPORT = re.compile(r"""from\s+['"]react['"]""")
_DEFAULT_EXPORT_NAME = re.compile(r"export\s+default\s+function\s+([A-Za-z_]\w*)")
_HTML_TITLE = re.compile(r"<title>\s*([^<]{1,80}?)\s*</title>", re.IGNORECASE)
# A leading `# …` or `// …` line, but not a shebang, preprocessor line, or pragma.
_LEADING_COMMENT = re.compile(r"\A\s*(?:#(?![!a-z])|//)\s*([^\n]{3,60})")
_SCRIPT_LANGUAGES = {"javascript", "js", "typescript", "ts"}
_LANGUAGE_LABELS = {
    "html": "HTML",
    "mermaid": "Diagram",
    "svg": "SVG",
    "css": "CSS",
    "sql": "SQL",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "cpp": "C++",
    "csharp": "C#",
}


@dataclass(frozen=True)
class SnippetArtifactShape:
    artifact_type: str
    title: str
    filename: str
    content_type: str
    language: str


def snippet_artifact_shape(language: str, content: str) -> SnippetArtifactShape:
    language = language.strip().lower()
    artifact_type, extension = SNIPPET_ARTIFACT_LANGUAGES.get(
        language, (ArtifactType.CODE, "txt")
    )
    # Models often fence React components as plain JavaScript.
    if language in _SCRIPT_LANGUAGES and _REACT_IMPORT.search(content):
        artifact_type = ArtifactType.REACT
        extension = "tsx" if language in {"typescript", "ts"} else "jsx"

    title, stem = _name_snippet(artifact_type, language, content)

    return SnippetArtifactShape(
        artifact_type=artifact_type,
        title=title,
        filename=f"{stem}.{extension}",
        content_type=ARTIFACT_CONTENT_TYPES[artifact_type],
        language=language or "text",
    )


def _name_snippet(artifact_type: str, language: str, content: str) -> tuple[str, str]:
    """(title, filename stem) from the code itself, falling back to the language."""
    component = _DEFAULT_EXPORT_NAME.search(content)
    if artifact_type == ArtifactType.REACT and component:
        return component.group(1), component.group(1)
    named = (
        _HTML_TITLE.search(content)
        if artifact_type == ArtifactType.HTML
        else _LEADING_COMMENT.search(content)
    )
    if named:
        title = named.group(1).strip()
        stem = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "snippet"
        return title, stem
    label = _LANGUAGE_LABELS.get(language, language.capitalize() or "Code")
    return f"{label} snippet", "snippet"
