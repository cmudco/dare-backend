"""
Memory API Views

REST API endpoints for cross-conversation memory management.
Uses synchronous DRF ViewSet with async service calls via async_to_sync.
"""

import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from memory.api import serializers as memory_serializers
from memory.services import get_memu_service

logger = logging.getLogger(__name__)


class MemoryViewSet(ViewSet):
    """
    ViewSet for memory management operations.

    Provides endpoints for:
    - Listing all memory items for the authenticated user
    - Retrieving a single memory item
    - Deleting a memory item
    - Searching memories via vector similarity
    - Importing memory items from user-provided JSON
    - Clearing all memories
    - Seeding demo data (development only)
    """

    permission_classes = [IsAuthenticated]

    def get_user_id(self) -> str:
        """Get the string user ID for MemU operations."""
        return str(self.request.user.id)

    def list(self, request):
        """
        List all memory items for the authenticated user.

        GET /api/memory/items/
        """
        try:
            service = get_memu_service()
            items = async_to_sync(service.list_items)(self.get_user_id())

            serializer = memory_serializers.MemoryItemSerializer(items, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to list memory items: {e}")
            return Response(
                {"error": "Failed to retrieve memories"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def retrieve(self, request, pk=None):
        """
        Retrieve a single memory item owned by the authenticated user.

        GET /api/memory/items/<id>/
        """
        try:
            service = get_memu_service()
            item = async_to_sync(service.get_item)(pk, self.get_user_id())

            if not item:
                return Response(
                    {"error": "Memory item not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            serializer = memory_serializers.MemoryItemSerializer(item)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to retrieve memory item {pk}: {e}")
            return Response(
                {"error": "Failed to retrieve memory item"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def destroy(self, request, pk=None):
        """
        Delete a memory item owned by the authenticated user.

        DELETE /api/memory/items/<id>/
        """
        try:
            service = get_memu_service()
            async_to_sync(service.delete_item)(pk, self.get_user_id())

            return Response(status=status.HTTP_204_NO_CONTENT)
        except PermissionError:
            return Response(
                {"error": "Memory item not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error(f"Failed to delete memory item {pk}: {e}")
            return Response(
                {"error": "Failed to delete memory item"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"], url_path="search")
    def search(self, request):
        """
        Search memories using vector similarity.

        POST /api/memory/search/
        Body: {"query": "search text"}
        """
        serializer = memory_serializers.MemorySearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        query = serializer.validated_data["query"]

        try:
            service = get_memu_service()
            results = async_to_sync(service.search)(self.get_user_id(), query)

            response_data = {
                "query": query,
                "items": results.get("items", []),
                "categories": results.get("categories", []),
            }

            response_serializer = memory_serializers.MemorySearchResponseSerializer(
                response_data
            )
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            return Response(
                {"error": "Failed to search memories"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"], url_path="import")
    def import_memories(self, request):
        """
        Import user-provided memory items.

        POST /api/memory/import/
        Body: {"items": [{"memoryType": "profile", "content": "..."}]}
        """
        serializer = memory_serializers.MemoryImportRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = get_memu_service()
        except Exception as e:
            logger.error(f"Memory service unavailable for import: {e}")
            return Response(
                {"error": "Failed to import memories"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        user_id = self.get_user_id()
        items_created = 0
        items_failed = 0
        for item in serializer.validated_data["items"]:
            # Tolerate per-item failures: imports are user-pasted batches, and
            # already-created items cannot be rolled back from MemU, so report
            # accurate counts instead of failing the whole request.
            try:
                async_to_sync(service.create_item)(
                    user_id,
                    item["memory_type"],
                    item["content"],
                    item["categories"],
                )
                items_created += 1
            except Exception as e:
                items_failed += 1
                logger.error(f"Failed to import memory item for user {user_id}: {e}")

        if items_created == 0:
            return Response(
                {"error": "Failed to import memories"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        noun = "memory" if items_created == 1 else "memories"
        message = f"Imported {items_created} {noun}"
        if items_failed:
            message += f" ({items_failed} failed)"

        response_serializer = memory_serializers.MemoryImportResponseSerializer(
            {
                "items_created": items_created,
                "items_failed": items_failed,
                "message": message,
            }
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["delete"], url_path="clear")
    def clear(self, request):
        """
        Clear all memory items for the authenticated user.

        DELETE /api/memory/clear/
        """
        try:
            service = get_memu_service()
            async_to_sync(service.clear_all)(self.get_user_id())

            serializer = memory_serializers.ClearResponseSerializer(
                {
                    "success": True,
                    "message": "All memories cleared successfully",
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            return Response(
                {"error": "Failed to clear memories"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"], url_path="seed")
    def seed(self, request):
        """
        Seed demo memory data for development/testing.
        Only available in DEBUG mode.

        POST /api/memory/seed/
        """
        # Only allow in development mode
        if not getattr(settings, "DEBUG", False):
            return Response(
                {"error": "Seeding is only available in development mode"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            service = get_memu_service()
            result = async_to_sync(service.seed_demo_data)(self.get_user_id())

            serializer = memory_serializers.SeedResponseSerializer(
                {
                    "items_created": result["items_created"],
                    "message": f"Successfully created {result['items_created']} demo memories",
                }
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Failed to seed demo data: {e}")
            return Response(
                {"error": "Failed to seed demo data"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
