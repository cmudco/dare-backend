"""
Sharing API Views

Endpoints for sharing items with specific users by email,
viewing shared items, managing recipients, and revoking shares.
"""
import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from sharing.api.serializers import (
    ShareRecipientSerializer,
    ShareRequestSerializer,
    ShareWithGroupRequestSerializer,
    SharedItemSerializer,
)
from sharing.models import SharedItem
from sharing.services.sharing_service import SharingService, SharingValidationError

logger = logging.getLogger(__name__)


class SharedItemViewSet(viewsets.GenericViewSet):
    """ViewSet for sharing items with specific users."""

    permission_classes = [IsAuthenticated]
    serializer_class = SharedItemSerializer

    def get_queryset(self):
        return SharedItem.active_objects.filter(
            shared_with=self.request.user,
        )

    def create(self, request):
        """
        Share an item with one or more users by email.

        POST /api/sharing/
        Body: { contentType, objectId, emails: [...], message? }
        """
        serializer = ShareRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = SharingService.share_item(
                entity_type=serializer.validated_data["content_type"],
                object_id=serializer.validated_data["object_id"],
                emails=serializer.validated_data["emails"],
                shared_by=request.user,
                message=serializer.validated_data.get("message", ""),
            )
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "shared": [
                    {"id": s.id, "email": s.email} for s in result.shared
                ],
                "failed": [
                    {"email": f.email, "reason": f.reason} for f in result.failed
                ],
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        """
        Revoke a specific share.

        DELETE /api/sharing/{id}/
        """
        try:
            SharingService.revoke_share(share_id=int(pk), user=request.user)
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="shared-with-me")
    def shared_with_me(self, request):
        """
        List items shared with the current user.

        GET /api/sharing/shared-with-me/
        GET /api/sharing/shared-with-me/?type=conversation
        """
        entity_type = request.query_params.get("type")

        try:
            qs = SharingService.get_shared_with_me(
                user=request.user,
                entity_type=entity_type,
            )
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SharedItemSerializer(qs, many=True)
        return Response({"results": serializer.data})

    @action(detail=False, methods=["get"], url_path="shared-by-me")
    def shared_by_me(self, request):
        """
        List items shared by the current user.

        GET /api/sharing/shared-by-me/
        GET /api/sharing/shared-by-me/?type=conversation
        """
        entity_type = request.query_params.get("type")

        try:
            qs = SharingService.get_shared_by_me(
                user=request.user,
                entity_type=entity_type,
            )
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SharedItemSerializer(qs, many=True)
        return Response({"results": serializer.data})

    @action(detail=False, methods=["post"], url_path="share-with-group")
    def share_with_group(self, request):
        """
        Share an item with all users in the requester's access code group.

        POST /api/sharing/share-with-group/
        Body: { contentType, objectId, message? }
        """
        serializer = ShareWithGroupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            shared_item = SharingService.share_with_access_code_group(
                entity_type=serializer.validated_data["content_type"],
                object_id=serializer.validated_data["object_id"],
                shared_by=request.user,
                message=serializer.validated_data.get("message", ""),
            )
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ShareRecipientSerializer(shared_item)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="recipients")
    def recipients(self, request):
        """
        List who an item has been shared with. Only the owner can view this.

        GET /api/sharing/recipients/?type=conversation&object_id=42
        """
        entity_type = request.query_params.get("type")
        object_id = request.query_params.get("object_id")

        if not entity_type or not object_id:
            return Response(
                {"error": "Both 'type' and 'object_id' query parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            qs = SharingService.get_recipients(
                entity_type=entity_type,
                object_id=object_id,
                user=request.user,
            )
        except SharingValidationError as e:
            return Response(
                {"error": str(e), "error_code": e.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ShareRecipientSerializer(qs, many=True)
        return Response({"results": serializer.data})
