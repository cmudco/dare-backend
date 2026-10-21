"""Provider code sandbox: the tool marker DARE requests and the files it carries.

When the sandbox is on, full-text files are uploaded to the provider's
container instead of being pasted into the prompt, so the model computes on
the whole file rather than reading a truncated copy.
"""

import hashlib
import mimetypes
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile

from core.storage.constants import StorageBackendChoice
from files.constants import FileStatus
from files.models import File, SandboxUpload

SANDBOX_TOOL_TYPE = "code_interpreter"
SANDBOX_TOOL_NAME = "code_sandbox"
OPENAI_PROVIDER = "openai"
# OpenAI bills each 1 GB container session (kept alive up to 20 idle minutes).
OPENAI_CONTAINER_FEE = Decimal("0.03")


@dataclass(frozen=True)
class SandboxFile:
    """A file the model produced in the sandbox, downloaded for DARE to keep."""

    filename: str
    content: bytes


def sandbox_tool(file_ids: Iterable[int], owner_ids: Iterable[int]) -> Dict:
    """The marker the provider service turns into its native sandbox tool."""
    return {
        "type": SANDBOX_TOOL_TYPE,
        "dare_file_ids": list(file_ids),
        "dare_owner_ids": [owner_id for owner_id in owner_ids if owner_id],
    }


def sandbox_files_note(names: List[str]) -> str:
    """Prompt text that stands in for inlined file contents."""
    listed = "\n".join(f"- {name}" for name in names)
    return (
        "These files are loaded in your Python sandbox under /mnt/data/ "
        "(each name there is prefixed with an upload id):\n"
        f"{listed}\n\n"
        "Answer questions about the data by running Python on the full files. "
        "Never estimate a number from memory or from a partial read. Save any "
        "chart you make as a PNG under /mnt/data/."
    )


def save_sandbox_file(sandbox_file: SandboxFile, user) -> File:
    """Keep a sandbox output as the user's generated file."""
    file_type = (
        mimetypes.guess_type(sandbox_file.filename)[0] or "application/octet-stream"
    )
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


class OpenAISandboxFiles:
    """Upload DARE files to OpenAI once per key, and fetch what the model made."""

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

    async def _provider_file_id(self, file: File) -> str:
        cached = await self._cached(file)
        if cached:
            return cached
        name = file.name or file.file.name
        content = await sync_to_async(self._read)(file)
        uploaded = await self.client.files.create(
            file=(name, content), purpose="user_data"
        )
        await sync_to_async(SandboxUpload.objects.update_or_create)(
            file=file,
            provider=OPENAI_PROVIDER,
            credential_fingerprint=self.fingerprint,
            defaults={"provider_file_id": uploaded.id},
        )
        return uploaded.id

    @sync_to_async
    def _cached(self, file: File) -> Optional[str]:
        return (
            SandboxUpload.objects.filter(
                file=file,
                provider=OPENAI_PROVIDER,
                credential_fingerprint=self.fingerprint,
            )
            .values_list("provider_file_id", flat=True)
            .first()
        )

    @staticmethod
    def _read(file: File) -> bytes:
        with file.file.open("rb") as handle:
            return handle.read()

    async def download(self, citations: List[Dict]) -> List[SandboxFile]:
        """Fetch each cited container file once."""
        downloaded: List[SandboxFile] = []
        seen = set()
        for citation in citations:
            key = (citation["container_id"], citation["file_id"])
            if key in seen:
                continue
            seen.add(key)
            response = await self.client.containers.files.content.retrieve(
                file_id=citation["file_id"], container_id=citation["container_id"]
            )
            downloaded.append(
                SandboxFile(
                    filename=citation.get("filename") or citation["file_id"],
                    content=await response.aread(),
                )
            )
        return downloaded
