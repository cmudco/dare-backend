"""Provider code sandbox: the tool marker DARE requests and the files it carries.

When the sandbox is on, full-text files are uploaded to the provider's
container instead of being pasted into the prompt, so the model computes on
the whole file rather than reading a truncated copy. OpenAI (code
interpreter) and Anthropic (code execution) each turn DARE's marker into
their own tool.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

import anthropic
import openai
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.utils import timezone

from conversations.constants import Provider
from core.services.api_key_service import (
    get_dispatch_credentials_for_user_sync,
    get_provider_api_key_sync,
)
from core.storage.constants import StorageBackendChoice
from files.constants import FileStatus
from files.models import File, SandboxUpload

logger = logging.getLogger(__name__)

SANDBOX_TOOL_TYPE = "dare_code_sandbox"
SANDBOX_TOOL_NAME = "code_sandbox"
OPENAI_PROVIDER = "openai"
ANTHROPIC_PROVIDER = "anthropic"

OPENAI_CODE_INTERPRETER = "code_interpreter"
# OpenAI bills each 1 GB container session (kept alive up to 20 idle minutes).
OPENAI_CONTAINER_FEE = Decimal("0.03")
# OpenAI deletes uploaded copies on its own after this; Anthropic has no expiry.
OPENAI_UPLOAD_LIFETIME = timedelta(days=7)
# A cached copy this close to expiry is re-uploaded so it can't vanish mid-turn.
UPLOAD_EXPIRY_MARGIN = timedelta(hours=1)

ANTHROPIC_CODE_EXECUTION_TOOL = {
    "type": "code_execution_20260521",
    "name": "code_execution",
}
ANTHROPIC_CODE_EXECUTION_SUBTOOLS = (
    "bash_code_execution",
    "text_editor_code_execution",
)
# Anthropic bills container time at $0.05/hour with a 5-minute minimum, and a
# request that carries files is billed even when no code runs.
ANTHROPIC_CONTAINER_FEE = Decimal("0.05") * 5 / 60

# Outputs are served back from DARE's own origin, so formats that can run
# script there (HTML, SVG) are never kept.
SANDBOX_OUTPUT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
SANDBOX_OUTPUT_MAX_BYTES = 20 * 1024 * 1024

_NOTE_LOCATIONS = {
    OPENAI_PROVIDER: (
        "under /mnt/data/ (each name there is prefixed with an upload id)",
        "Save any chart you make as a PNG under /mnt/data/.",
    ),
    ANTHROPIC_PROVIDER: (
        "in the folder named by $INPUT_DIR",
        "Save any chart you make as a PNG in $OUTPUT_DIR. Saved charts are "
        "shown with your answer, so never mention their path. Finish every "
        "code run, charts included, before you write; then give the full "
        "answer in one final message.",
    ),
}


@dataclass(frozen=True)
class SandboxScope:
    """Which provider runs the sandbox and whose files may enter it."""

    provider: str
    owner_ids: List[int]


@dataclass(frozen=True)
class SandboxFile:
    """A file the model produced in the sandbox, downloaded for DARE to keep."""

    filename: str
    content: bytes


def sandbox_tool(file_ids: Iterable[int], scope: SandboxScope) -> Dict:
    """The marker the provider service turns into its native sandbox tool."""
    return {
        "type": SANDBOX_TOOL_TYPE,
        "dare_file_ids": list(file_ids),
        "dare_owner_ids": list(scope.owner_ids),
    }


def sandbox_marker(tools: Optional[List[Dict]]) -> Optional[Dict]:
    return next(
        (tool for tool in tools or [] if tool.get("type") == SANDBOX_TOOL_TYPE),
        None,
    )


def sandbox_files_note(names: List[str], provider: str) -> str:
    """Prompt text that stands in for inlined file contents."""
    location, save_hint = _NOTE_LOCATIONS[provider]
    listed = "\n".join(f"- {name}" for name in names)
    return (
        f"These files are loaded in your Python sandbox {location}:\n"
        f"{listed}\n\n"
        "Answer questions about the data by running Python on the full files. "
        f"Never estimate a number from memory or from a partial read. {save_hint}"
    )


def sandbox_output(filename: Optional[str], content: bytes) -> Optional[SandboxFile]:
    """A sandbox output DARE may keep, or None for an unsafe or oversized one."""
    name = sandbox_output_name(filename)
    if not name or len(content) > SANDBOX_OUTPUT_MAX_BYTES:
        return None
    return SandboxFile(filename=name, content=content)


def sandbox_output_name(filename: Optional[str]) -> Optional[str]:
    name = PurePosixPath(filename or "").name
    if PurePosixPath(name).suffix.lower() not in SANDBOX_OUTPUT_TYPES:
        return None
    return name


# Tags a reply's sandbox outputs so regeneration can replace exactly those.
SANDBOX_OUTPUT_MARKER = {"source": SANDBOX_TOOL_NAME}


def sandbox_outputs(message) -> List[File]:
    return list(message.files.filter(generation_params=SANDBOX_OUTPUT_MARKER))


def save_sandbox_file(sandbox_file: SandboxFile, user) -> File:
    """Keep a sandbox output as the user's generated file."""
    file_type = SANDBOX_OUTPUT_TYPES[
        PurePosixPath(sandbox_file.filename).suffix.lower()
    ]
    is_image = file_type.startswith("image/")
    file_obj = File(
        user=user,
        name=sandbox_file.filename,
        file_type=file_type,
        size=len(sandbox_file.content),
        status=FileStatus.PROCESSED,
        is_media=is_image,
        media_type="generated_image" if is_image else None,
        is_generated=True,
        generation_params=SANDBOX_OUTPUT_MARKER,
        storage_backend=(user.storage_backend if user else StorageBackendChoice.LOCAL),
    )
    file_obj.file.save(
        sandbox_file.filename, ContentFile(sandbox_file.content), save=False
    )
    file_obj.save()
    return file_obj


def credential_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:32]


@sync_to_async
def sandbox_file_names(file_ids: List[int], owner_ids: List[int]) -> List[str]:
    return [
        file.name or file.file.name
        for file in File.active_objects.filter(id__in=file_ids, user_id__in=owner_ids)
    ]


class SandboxFiles:
    """Upload DARE files to a provider once per key."""

    provider: str

    def __init__(self, client, api_key: str):
        self.client = client
        self.fingerprint = credential_fingerprint(api_key)

    async def provider_file_ids(
        self, file_ids: List[int], owner_ids: List[int]
    ) -> List[str]:
        files = await sync_to_async(list)(
            File.active_objects.filter(id__in=file_ids, user_id__in=owner_ids)
        )
        return [await self._provider_file_id(file) for file in files]

    async def _upload(
        self, name: str, content: bytes
    ) -> Tuple[str, Optional[datetime]]:
        """Upload a copy; return its provider id and when it expires."""
        raise NotImplementedError

    async def _delete(self, provider_file_id: str) -> None:
        raise NotImplementedError

    async def _provider_file_id(self, file: File) -> str:
        previous = await self._previous(file)
        if previous and self._still_valid(previous):
            return previous.provider_file_id
        name = file.name or file.file.name
        content = await sync_to_async(self._read)(file)
        provider_file_id, expires_at = await self._upload(name, content)
        await sync_to_async(SandboxUpload.objects.update_or_create)(
            file=file,
            provider=self.provider,
            credential_fingerprint=self.fingerprint,
            defaults={"provider_file_id": provider_file_id, "expires_at": expires_at},
        )
        if previous:
            # An expired copy is usually gone already; one without an expiry
            # would otherwise stay at the provider forever.
            await self._delete_quietly(previous.provider_file_id)
        return provider_file_id

    @sync_to_async
    def _previous(self, file: File) -> Optional[SandboxUpload]:
        return SandboxUpload.objects.filter(
            file=file,
            provider=self.provider,
            credential_fingerprint=self.fingerprint,
        ).first()

    def _still_valid(self, upload: SandboxUpload) -> bool:
        if upload.expires_at is None:
            return self.provider == ANTHROPIC_PROVIDER
        return upload.expires_at > timezone.now() + UPLOAD_EXPIRY_MARGIN

    async def _delete_quietly(self, provider_file_id: str) -> None:
        try:
            await self._delete(provider_file_id)
        except (openai.NotFoundError, anthropic.NotFoundError):
            pass
        except Exception:
            logger.warning(
                "Could not delete %s sandbox copy %s",
                self.provider,
                provider_file_id,
                exc_info=True,
            )

    @staticmethod
    def _read(file: File) -> bytes:
        with file.file.open("rb") as handle:
            return handle.read()


class OpenAISandboxFiles(SandboxFiles):
    provider = OPENAI_PROVIDER

    async def _upload(self, name: str, content: bytes) -> Tuple[str, datetime]:
        uploaded = await self.client.files.create(
            file=(name, content),
            purpose="user_data",
            expires_after={
                "anchor": "created_at",
                "seconds": int(OPENAI_UPLOAD_LIFETIME.total_seconds()),
            },
        )
        return uploaded.id, timezone.now() + OPENAI_UPLOAD_LIFETIME

    async def _delete(self, provider_file_id: str) -> None:
        await self.client.files.delete(provider_file_id)

    async def download(self, citations: List[Dict]) -> List[SandboxFile]:
        """Fetch each cited container file once."""
        downloaded: List[SandboxFile] = []
        seen = set()
        for citation in citations:
            key = (citation["container_id"], citation["file_id"])
            filename = citation.get("filename") or citation["file_id"]
            if key in seen or not sandbox_output_name(filename):
                continue
            seen.add(key)
            response = await self.client.containers.files.content.retrieve(
                file_id=citation["file_id"], container_id=citation["container_id"]
            )
            kept = sandbox_output(filename, await response.aread())
            if kept:
                downloaded.append(kept)
        return downloaded


class AnthropicSandboxFiles(SandboxFiles):
    provider = ANTHROPIC_PROVIDER

    async def _upload(self, name: str, content: bytes) -> Tuple[str, None]:
        uploaded = await self.client.beta.files.upload(file=(name, content))
        return uploaded.id, None

    async def _delete(self, provider_file_id: str) -> None:
        await self.client.beta.files.delete(provider_file_id)

    async def download(self, file_ids: List[str]) -> List[SandboxFile]:
        """Fetch each file the model left in its output folder once, then
        delete Anthropic's copy: DARE keeps its own, and Anthropic keeps
        generated files until they are deleted."""
        downloaded: List[SandboxFile] = []
        for file_id in dict.fromkeys(file_ids):
            try:
                metadata = await self.client.beta.files.retrieve_metadata(file_id)
                if sandbox_output_name(metadata.filename):
                    response = await self.client.beta.files.download(file_id)
                    kept = sandbox_output(metadata.filename, await response.read())
                    if kept:
                        downloaded.append(kept)
            finally:
                await self._delete_quietly(file_id)
        return downloaded


def attach_container_uploads(messages: List[Dict], file_ids: List[str]) -> None:
    """Put the uploaded files on the latest user turn (Anthropic).

    A fresh container only loads uploads from the newest user turn; earlier
    turns' uploads are assumed to be in a container already.
    """
    last_assistant = max(
        (i for i, message in enumerate(messages) if message.get("role") == "assistant"),
        default=-1,
    )
    turn = next(
        (m for m in messages[last_assistant + 1 :] if m.get("role") == "user"), None
    )
    if not file_ids or turn is None:
        return
    content = turn.get("content")
    blocks = (
        [{"type": "text", "text": content}]
        if isinstance(content, str)
        else list(content or [])
    )
    uploads = [{"type": "container_upload", "file_id": fid} for fid in file_ids]
    # Tool results must lead the user turn that answers a tool call.
    results = [block for block in blocks if block.get("type") == "tool_result"]
    rest = [block for block in blocks if block.get("type") != "tool_result"]
    turn["content"] = [*results, *uploads, *rest]


_DARE_PROVIDER = {
    OPENAI_PROVIDER: Provider.OPENAI.value,
    ANTHROPIC_PROVIDER: Provider.CLAUDE.value,
}


def provider_copies(file_id: int, using: str = "default") -> List[Dict]:
    """The provider copies of a DARE file, as plain data for a background job."""
    return list(
        SandboxUpload.objects.using(using)
        .filter(file_id=file_id)
        .values("provider", "credential_fingerprint", "provider_file_id")
    )


def delete_provider_copies(copies: List[Dict], owner_id: Optional[int]) -> None:
    """Delete a removed file's copies at OpenAI and Anthropic.

    A copy uploaded with a key DARE can no longer resolve (a deleted user's
    own key) is left behind; OpenAI copies still expire on their own.
    """
    for copy in copies:
        api_key = _uploading_key(
            copy["provider"], copy["credential_fingerprint"], owner_id
        )
        if api_key is None:
            logger.warning(
                "No key left to delete %s copy %s",
                copy["provider"],
                copy["provider_file_id"],
            )
            continue
        try:
            if copy["provider"] == OPENAI_PROVIDER:
                openai.OpenAI(api_key=api_key).files.delete(copy["provider_file_id"])
            else:
                anthropic.Anthropic(api_key=api_key).beta.files.delete(
                    copy["provider_file_id"]
                )
        except (openai.NotFoundError, anthropic.NotFoundError):
            pass  # Already expired or deleted.


def _uploading_key(
    provider: str, fingerprint: str, owner_id: Optional[int]
) -> Optional[str]:
    """The key whose fingerprint the copy was stored under, if DARE still has it."""
    from users.models import User

    dare_provider = _DARE_PROVIDER[provider]
    resolvers = [lambda: get_provider_api_key_sync(dare_provider)]
    owner = User.objects.filter(pk=owner_id).first() if owner_id else None
    if owner is not None:
        resolvers.append(
            lambda: get_dispatch_credentials_for_user_sync(dare_provider, owner).api_key
        )
    for resolve in resolvers:
        try:
            api_key = resolve()
        except Exception:
            continue
        if api_key and credential_fingerprint(api_key) == fingerprint:
            return api_key
    return None
