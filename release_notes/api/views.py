from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.pagination import CustomPageNumberPagination
from release_notes.api.serializers import ReleaseNoteSerializer
from release_notes.services.release_note_service import (
    live_release_notes,
    mark_seen,
    unseen_release_notes,
)
from users.utils import detect_platform_from_request


class ReleaseNoteViewSet(viewsets.ReadOnlyModelViewSet):
    """Live release notes for the requesting platform; authored in Django admin."""

    serializer_class = ReleaseNoteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        return live_release_notes(detect_platform_from_request(self.request))

    @action(detail=False, methods=["get"])
    def unseen(self, request):
        notes = unseen_release_notes(
            request.user, detect_platform_from_request(request)
        )
        return Response(self.get_serializer(notes, many=True).data)

    @action(detail=True, methods=["post"])
    def seen(self, request, pk=None):
        mark_seen(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
