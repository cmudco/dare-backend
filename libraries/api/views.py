from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from libraries.api.serializers import SharedLibrarySerializer
from libraries.models import SharedLibrary, UserLibraryAccess


class SharedLibraryViewSet(viewsets.ReadOnlyModelViewSet):
    """Catalog of shared libraries, plus add/remove for the current user.

    The vector store is never touched here — adding a library is purely a
    relational entitlement (``UserLibraryAccess``).
    """

    serializer_class = SharedLibrarySerializer
    permission_classes = [IsAuthenticated]
    queryset = SharedLibrary.active_objects.all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["added_library_ids"] = set(
            UserLibraryAccess.active_objects.filter(user=self.request.user).values_list(
                "library_id", flat=True
            )
        )
        return context

    @action(detail=True, methods=["post"])
    def add(self, request, pk=None):
        library = self.get_object()
        UserLibraryAccess.objects.get_or_create(user=request.user, library=library)
        return Response({"detail": "Added to library."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["delete"])
    def remove(self, request, pk=None):
        library = self.get_object()
        UserLibraryAccess.objects.filter(user=request.user, library=library).delete()
        return Response({"detail": "Removed from library."}, status=status.HTTP_200_OK)
