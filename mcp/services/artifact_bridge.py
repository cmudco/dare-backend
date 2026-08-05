"""Import PDF resources returned by MCP tools into DARE artifacts."""

import base64
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx
from asgiref.sync import sync_to_async

from conversations.constants import ArtifactType
from conversations.models import Artifact, Conversation, Message
from conversations.services.artifact_service import (
    create_artifact,
    create_artifact_version,
)
from mcp.models import MCPServer

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 30.0
MAX_PDF_BYTES = 15 * 1024 * 1024

_QUILL_RE = re.compile(r"^\$?(?:quill|QUILL):\s*(\S+)", re.MULTILINE)
_TITLE_RES = [
    re.compile(r"^subject:\s*(.+)$", re.MULTILINE),
    re.compile(r"^title:\s*(.+)$", re.MULTILINE),
    re.compile(r"^headline:\s*(.+)$", re.MULTILINE),
]


class BridgeStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    CREATED = "created"
    FAILED = "failed"


@dataclass(frozen=True)
class BridgeResult:
    status: BridgeStatus
    artifact: Optional[Dict[str, Any]] = None
    error: str = ""


def _detect_pdf_url(result: Any) -> Optional[str]:
    """Return a declared PDF URL from an MCP result."""
    if not isinstance(result, dict):
        return None

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        mime = (structured.get("mimeType") or "").split(";", 1)[0].lower()
        url = structured.get("url")
        if isinstance(url, str) and mime == "application/pdf":
            return url

    content = result.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "resource_link":
            continue
        mime = (block.get("mimeType") or "").split(";", 1)[0].lower()
        uri = block.get("uri") or block.get("url")
        if isinstance(uri, str) and mime == "application/pdf":
            return uri
    return None


def _extract_document_meta(arguments: Dict) -> Dict[str, str]:
    """Pull a human title and quill ref from create_document content."""
    content = arguments.get("content", "") if isinstance(arguments, dict) else ""
    if not isinstance(content, str):
        content = str(content)

    quill_match = _QUILL_RE.search(content)
    quill = quill_match.group(1).strip() if quill_match else ""
    title = ""
    for pattern in _TITLE_RES:
        title_match = pattern.search(content)
        if title_match:
            title = title_match.group(1).strip().strip("\"'")
            break
    return {"quill": quill, "title": title or "CMU Document"}


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP artifact URL must be an absolute HTTP(S) URL")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("MCP artifact URL has an invalid port") from exc
    return parsed.scheme, parsed.hostname.lower(), port


def _validate_artifact_url(url: str, server_url: str) -> None:
    """Only fetch artifacts from the configured MCP server's origin."""
    if _origin(url) != _origin(server_url):
        raise ValueError("MCP artifact URL is not on the configured server origin")


async def _fetch_pdf(url: str, server_url: str) -> bytes:
    _validate_artifact_url(url, server_url)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/pdf":
                raise ValueError("MCP artifact response is not application/pdf")
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise ValueError(
                        "MCP artifact returned an invalid content length"
                    ) from exc
                if declared_length < 0:
                    raise ValueError("MCP artifact returned an invalid content length")
                if declared_length > MAX_PDF_BYTES:
                    raise ValueError("MCP PDF exceeds the 15 MB import limit")

            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise ValueError("MCP PDF exceeds the 15 MB import limit")
                chunks.append(chunk)

    data = b"".join(chunks)
    if not data.startswith(b"%PDF-"):
        raise ValueError("MCP artifact response does not contain a valid PDF header")
    return data


@sync_to_async
def _get_server_url(server_slug: str) -> str:
    server = (
        MCPServer.active_objects.filter(slug=server_slug).only("remote_url").first()
    )
    if not server or not server.remote_url:
        raise ValueError("The MCP server has no configured remote URL")
    return server.remote_url


@sync_to_async
def _find_existing_artifact(
    conversation: Conversation,
    source_tool: str,
    quill: str,
    title: str,
    current_message: Message,
) -> Optional[Artifact]:
    """Find an earlier, unambiguous document to version."""
    if not quill or title == "CMU Document":
        return None
    queryset = Artifact.active_objects.filter(
        conversation=conversation,
        source_tool=source_tool,
        artifact_type=ArtifactType.PDF,
        title=title,
        metadata__quill=quill,
    ).order_by("-created_at")
    if current_message is not None:
        queryset = queryset.exclude(message=current_message)
    return queryset.first()


# Kept as a small public compatibility wrapper for existing tests/callers.
async def _create_version(
    existing: Artifact,
    message: Message,
    content: str,
    title: str,
    filename: str,
    metadata: Dict,
) -> Artifact:
    return await create_artifact_version(
        existing=existing,
        message=message,
        content=content,
        title=title,
        filename=filename,
        content_type="application/pdf",
        metadata=metadata,
    )


async def maybe_create_pdf_artifact(
    result: Any,
    *,
    message: Message,
    conversation: Conversation,
    arguments: Dict,
    server_slug: str,
    tool_name: str,
    send_callback: Callable,
) -> BridgeResult:
    """Import a PDF result, returning an explicit bridge outcome."""
    url = _detect_pdf_url(result)
    if not url:
        return BridgeResult(BridgeStatus.NOT_APPLICABLE)

    try:
        server_url = await _get_server_url(server_slug)
        pdf_bytes = await _fetch_pdf(url, server_url)
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
        if existing:
            artifact = await _create_version(
                existing, message, data_uri, meta["title"], filename, metadata
            )
            event_type = "artifact_updated"
        else:
            artifact = await create_artifact(
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
            "contentType": artifact.content_type,
            "content": artifact.content,
            "artifactType": artifact.artifact_type,
            "version": artifact.version,
            "metadata": artifact.metadata,
        }
        callback_result = send_callback(event)
        if hasattr(callback_result, "__await__"):
            await callback_result

        bridged = {
            "artifact_id": artifact.id,
            "title": artifact.title,
            "filename": artifact.filename,
            "version": artifact.version,
        }
        logger.info(
            "[ArtifactBridge] Imported %s into artifact %s (v%s, %d bytes)",
            source_tool,
            artifact.id,
            artifact.version,
            len(pdf_bytes),
        )
        return BridgeResult(BridgeStatus.CREATED, artifact=bridged)
    except Exception:
        logger.exception("[ArtifactBridge] Failed to import PDF result")
        return BridgeResult(
            BridgeStatus.FAILED,
            error=(
                "The rendered PDF could not be imported into DARE. "
                "Check the MCP renderer and retry."
            ),
        )
