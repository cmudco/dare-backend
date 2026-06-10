from typing import Any

from asgiref.sync import async_to_sync

from data_exports.services.serialization_helpers import to_iso
from memory.services import get_memu_service


class MemoryExportBuilder:
    """Build export payloads from the user's MemU memory store."""

    def build(self, user_id: str) -> list[dict[str, Any]]:
        service = get_memu_service()
        items = async_to_sync(service.list_items)(user_id)
        return [self._serialize_memory_item(item) for item in items]

    def _serialize_memory_item(self, item: dict[str, Any]) -> dict[str, Any]:
        content = (
            item.get("summary")
            or item.get("content")
            or item.get("memory_content")
            or ""
        )
        payload = {
            "id": str(item.get("id", "")),
            "memoryType": item.get("memory_type")
            or item.get("memoryType")
            or "unknown",
            "content": content,
            "categories": self._normalize_categories(item.get("categories", [])),
            "createdAt": to_iso(item.get("created_at") or item.get("createdAt")),
            "updatedAt": to_iso(item.get("updated_at") or item.get("updatedAt")),
        }

        if item.get("score") is not None:
            payload["score"] = item.get("score")

        return payload

    def _normalize_categories(self, categories: Any) -> list[str]:
        if not isinstance(categories, list):
            return []
        return [str(category) for category in categories]
