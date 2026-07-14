"""
MCP result → PDF Artifact bridge.

Some MCP servers (e.g. quillmark) render documents and return a hosted URL in
their tool result. Those URLs point inside the Docker network and often force
`Content-Disposition: attachment`, so they are useless to the browser. This
bridge detects a PDF result generically, fetches the bytes server-side, and
persists them as a DARE Artifact (base64 data URI in ``Artifact.content``) so
the frontend previews the document inline and the normal artifact lifecycle
(versioning, download, reload) applies.

Detection is protocol-shaped, not server-shaped: any MCP tool result whose
``structuredContent`` (or ``resource_link`` content block) carries a URL with
an ``application/pdf`` mime type is bridged. Failure of the bridge must never
fail the tool call — callers wrap it and fall back to text-only behavior.
"""

import base64
import logging
import re
from typing import Any, Callable, Dict, Optional

import httpx
from asgiref.sync import sync_to_async

from conversations.constants import ArtifactStatus, ArtifactType
from conversations.models import Artifact, Conversation, Message

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30.0
MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MB safety cap

# card-yaml `$quill:` is the current syntax; bare `QUILL:` kept for older content.
_QUILL_RE = re.compile(r"^\$?(?:quill|QUILL):\s*(\S+)", re.MULTILINE)
_TITLE_RES = [
    re.compile(r"^subject:\s*(.+)$", re.MULTILINE),
    re.compile(r"^title:\s*(.+)$", re.MULTILINE),
    re.compile(r"^headline:\s*(.+)$", re.MULTILINE),
]


def _detect_pdf_url(result: Any) -> Optional[str]:
    """Return the PDF URL from an MCP tool result, or None."""
    if not isinstance(result, dict):
        return None

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        mime = (structured.get("mimeType") or "").lower()
        url = structured.get("url")
        if url and mime == "application/pdf":
            return url

    for block in result.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "resource_link":
            mime = (block.get("mimeType") or "").lower()
            uri = block.get("uri") or block.get("url")
            if uri and mime == "application/pdf":
                return uri
    return None


def _extract_document_meta(arguments: Dict) -> Dict[str, str]:
    """Pull a human title and quill ref out of the tool-call content string."""
    content = arguments.get("content", "") if isinstance(arguments, dict) else ""
    if not isinstance(content, str):
        content = str(content)

    quill = ""
    match = _QUILL_RE.search(content)
    if match:
        quill = match.group(1).strip()

    title = ""
    for pattern in _TITLE_RES:
        match = pattern.search(content)
        if match:
            title = match.group(1).strip().strip("\"'")
            break

    return {"quill": quill, "title": title or "CMU Document"}


async def _fetch_pdf(url: str) -> Optional[bytes]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.content
        if len(data) > MAX_PDF_BYTES:
            logger.warning(
                "[ArtifactBridge] PDF too large to bridge (%d bytes) from %s",
                len(data),
                url,
            )
            return None
        return data


@sync_to_async
def _find_existing_artifact(
    conversation: Conversation,
    source_tool: str,
    quill: str,
    title: str,
    current_message: Message,
) -> Optional[Artifact]:
    """Prior artifact that this render is an edit OF, or None for a new doc.

    A render only versions an existing artifact when the quill AND the title
    match a document from an EARLIER message. Same-message renders never
    match — one prompt may legitimately produce several documents (even from
    the same template), and they must not clobber each other into a single
    version chain. A title change starts a new artifact rather than a new
    version, which keeps distinct documents distinct at the cost of splitting
    history on retitling edits.
    """
    queryset = Artifact.active_objects.filter(
        conversation=conversation,
        source_tool=source_tool,
        artifact_type=ArtifactType.PDF,
        title=title,
    ).order_by("-created_at")
    if quill:
        queryset = queryset.filter(metadata__quill=quill)
    if current_message is not None:
        queryset = queryset.exclude(message=current_message)
    return queryset.first()


@sync_to_async
def _create_version(
    existing: Artifact,
    message: Message,
    content: str,
    title: str,
    filename: str,
    metadata: Dict,
) -> Artifact:
    # Version off the group's newest artifact, not the matched one: when a
    # round renders the same title more than once, each render matched the
    # same pre-round artifact and would otherwise mint duplicate version
    # numbers (three "v2"s instead of v2 → v3 → v4).
    latest = (
        Artifact.active_objects.filter(artifact_group=existing.artifact_group)
        .order_by("-version")
        .first()
    )
    new_artifact = (latest or existing).create_new_version()
    new_artifact.message = message
    new_artifact.content = content
    new_artifact.title = title
    new_artifact.filename = filename
    new_artifact.metadata = metadata
    new_artifact.status = ArtifactStatus.COMPLETED
    new_artifact.save(
        update_fields=[
            "message",
            "content",
            "title",
            "filename",
            "metadata",
            "status",
        ]
    )
    return new_artifact


async def maybe_create_pdf_artifact(
    result: Any,
    *,
    message: Message,
    conversation: Conversation,
    arguments: Dict,
    server_slug: str,
    tool_name: str,
    send_callback: Callable,
) -> Optional[Dict[str, Any]]:
    """
    Bridge a PDF-producing MCP tool result into a DARE Artifact.

    Returns ``{"artifact_id", "title", "filename", "version"}`` when a PDF was
    bridged, or None when the result is not a PDF document. Raises nothing:
    callers rely on this degrading silently to text-only behavior.
    """
    try:
        url = _detect_pdf_url(result)
        if not url:
            return None

        pdf_bytes = await _fetch_pdf(url)
        if not pdf_bytes:
            return None

        meta = _extract_document_meta(arguments)
        data_uri = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode(
            "ascii"
        )
        source_tool = f"{server_slug}__{tool_name}"
        filename_stub = re.sub(r"[^a-z0-9]+", "-", meta["title"].lower()).strip("-")
        filename = f"{filename_stub or 'document'}.pdf"
        metadata = {
            "quill": meta["quill"],
            "serverSlug": server_slug,
            "toolName": tool_name,
            "sourceUrl": url,
        }

        existing = await _find_existing_artifact(
            conversation, source_tool, meta["quill"], meta["title"], message
        )
        # Imported lazily: artifact_tool_executor pulls in the conversations
        # service stack, which circularly imports mcp.services at module load
        # (fine at runtime, breaks test discovery).
        from conversations.services.artifact_tool_executor import (
            artifact_tool_executor,
        )

        if existing:
            artifact = await _create_version(
                existing, message, data_uri, meta["title"], filename, metadata
            )
            event_type = "artifact_updated"
        else:
            artifact = await artifact_tool_executor._create_artifact(
                conversation=conversation,
                message=message,
                title=meta["title"],
                content=data_uri,
                artifact_type=ArtifactType.PDF,
                filename=filename,
                content_type="application/pdf",
                source_tool=source_tool,
                metadata=metadata,
            )
            event_type = "artifact_created"

        event = {
            "type": event_type,
            "artifactId": artifact.id,
            "messageId": message.id if message else None,
            "artifactGroupId": artifact.artifact_group_id,
            "filename": artifact.filename,
            "title": artifact.title,
            "contentType": "application/pdf",
            "content": artifact.content,
            "artifactType": artifact.artifact_type,
            "version": artifact.version,
            "metadata": artifact.metadata,
        }
        maybe_awaitable = send_callback(event)
        if hasattr(maybe_awaitable, "__await__"):
            await maybe_awaitable

        logger.info(
            "[ArtifactBridge] Bridged PDF from %s into artifact %s (v%s, %d bytes)",
            source_tool,
            artifact.id,
            artifact.version,
            len(pdf_bytes),
        )
        return {
            "artifact_id": artifact.id,
            "title": artifact.title,
            "filename": artifact.filename,
            "version": artifact.version,
        }
    except Exception:
        logger.exception("[ArtifactBridge] Failed to bridge PDF result")
        return None
