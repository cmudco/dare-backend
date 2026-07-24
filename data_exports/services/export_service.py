import logging
from typing import Any

import data_exports.services.conversation_export_builder as conv_builder
import data_exports.services.memory_export_builder as mem_builder
import data_exports.services.serialization_helpers as export_serialization
from data_exports.services.constants import DataExportScope
from data_exports.services.dtos import DataExportRequest, DataExportResult
from data_exports.services.zip_writer import ZipArchiveWriter, ZipEntry

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "dare-export-v1"

MEMORY_UNAVAILABLE_NOTE = (
    "Memories could not be retrieved because the memory store was unavailable; "
    "memory.json contains no items."
)


class DataExportService:
    """Orchestrate DARE context export generation."""

    def __init__(
        self,
        memory_builder: mem_builder.MemoryExportBuilder | None = None,
        conversation_builder: conv_builder.ConversationExportBuilder | None = None,
        zip_writer: ZipArchiveWriter | None = None,
    ) -> None:
        self.memory_builder = memory_builder or mem_builder.MemoryExportBuilder()
        self.conversation_builder = (
            conversation_builder or conv_builder.ConversationExportBuilder()
        )
        self.zip_writer = zip_writer or ZipArchiveWriter()

    def generate_export(self, request: DataExportRequest) -> DataExportResult:
        memories, memory_note = self._build_memories(request)
        conversations = []
        if request.scope == DataExportScope.FULL:
            conversations = self.conversation_builder.build(request.user)

        manifest = self._build_manifest(request, memories, conversations, memory_note)
        entries = self._build_zip_entries(request, manifest, memories, conversations)
        content = self.zip_writer.write(entries)

        return DataExportResult(
            filename=self._build_filename(request),
            content=content,
        )

    def _build_memories(
        self, request: DataExportRequest
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Build memory rows, degrading gracefully for full exports.

        A memories-only export is worthless without memories, so the failure
        propagates. A full export still has value (user profile and
        conversations), so it proceeds with an explicit note in the manifest.
        """
        try:
            return self.memory_builder.build(str(request.user.id)), None
        except Exception as exc:
            if request.scope == DataExportScope.MEMORIES:
                raise
            logger.warning(
                "Memory store unavailable during full export for user %s: %s",
                request.user.id,
                exc,
            )
            return [], MEMORY_UNAVAILABLE_NOTE

    def _build_zip_entries(
        self,
        request: DataExportRequest,
        manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        conversations: list[dict[str, Any]],
    ) -> list[ZipEntry]:
        entries = [
            ZipEntry(
                "dare-export/manifest.json",
                export_serialization.json_dumps(manifest),
            ),
            ZipEntry(
                "dare-export/user.json",
                export_serialization.json_dumps(self._build_user_payload(request)),
            ),
            ZipEntry(
                "dare-export/memory.json",
                export_serialization.json_dumps(
                    self._build_memory_payload(request, memories)
                ),
            ),
        ]

        if request.scope == DataExportScope.FULL:
            entries.append(
                ZipEntry(
                    "dare-export/conversations.json",
                    export_serialization.json_dumps(conversations),
                )
            )

        return entries

    def _build_manifest(
        self,
        request: DataExportRequest,
        memories: list[dict[str, Any]],
        conversations: list[dict[str, Any]],
        memory_note: str | None = None,
    ) -> dict[str, Any]:
        message_count = sum(len(item["chatMessages"]) for item in conversations)
        excluded_content = [
            "Uploaded file binaries are not included in this export.",
            "Secrets, provider API keys, internal keys, and production credentials are not included.",
            "Deleted or inactive conversations and messages are not included.",
        ]
        if memory_note:
            excluded_content.append(memory_note)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "exportedAt": export_serialization.to_iso(request.generated_at),
            "scope": request.scope.value,
            "user": {
                "accountId": request.user.id,
            },
            "counts": {
                "memories": len(memories),
                "conversations": len(conversations),
                "messages": message_count,
            },
            "excludedContent": excluded_content,
        }

    def _build_user_payload(self, request: DataExportRequest) -> dict[str, Any]:
        user = request.user
        return {
            "accountId": user.id,
            "fullName": user.get_full_name(),
            "email": user.email,
            "role": user.role,
            "industry": user.industry,
            "purpose": user.purpose,
            "referralSource": user.referral_source,
            "platformRole": user.platform_role,
        }

    def _build_memory_payload(
        self,
        request: DataExportRequest,
        memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "accountId": request.user.id,
            "memory": {
                "profile": {},
                "items": memories,
            },
        }

    def _build_filename(self, request: DataExportRequest) -> str:
        timestamp = request.generated_at.strftime("%Y%m%d-%H%M%S")
        return f"dare-context-export-{timestamp}.zip"
