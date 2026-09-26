from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from projects.api.serializers import PersonalProjectSerializer
from projects.services.project_service import delete_project, owned_projects


class DeleteProjectQuerySerializer(serializers.Serializer):
    delete_conversations = serializers.BooleanField(default=False)


class PersonalProjectViewSet(viewsets.ModelViewSet):
    """CRUD for the requesting user's personal projects."""

    serializer_class = PersonalProjectSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    pagination_class = None

    def get_queryset(self):
        return owned_projects(self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save(user=request.user)
        return Response(self._reloaded(project.pk), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            self.get_object(), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        project = serializer.save()
        return Response(self._reloaded(project.pk))

    def destroy(self, request, *args, **kwargs):
        query = DeleteProjectQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        delete_project(
            self.get_object(),
            delete_conversations=query.validated_data["delete_conversations"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _reloaded(self, pk: int) -> dict:
        """Re-read through the annotated queryset so counts are in the payload."""
        return self.get_serializer(self.get_queryset().get(pk=pk)).data
