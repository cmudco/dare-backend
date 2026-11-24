from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from projects.api.serializers import (
    PersonalProjectSerializer,
    SourceSizeRequestSerializer,
    SourceSizeSerializer,
)
from files.services.source_size import estimate_source_size
from projects.services.project_service import (
    delete_project,
    full_files_token_budget,
    owned_projects,
    selected_source_files,
)


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

    @action(detail=False, methods=["post"], url_path="source-size")
    def source_size(self, request):
        """Estimate the tokens of reading the given files and folders in full."""
        query = SourceSizeRequestSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        query.is_valid(raise_exception=True)
        files = selected_source_files(
            request.user,
            [file.pk for file in query.validated_data["file_ids"]],
            [folder.pk for folder in query.validated_data["folder_ids"]],
        )
        size = estimate_source_size(files)
        token_budget = full_files_token_budget(query.validated_data["model_ref"])
        return Response(
            SourceSizeSerializer(
                {
                    "file_count": size.file_count,
                    "estimated_tokens": size.estimated_tokens,
                    "token_limit": token_budget,
                    "fits_full_files": size.fits(token_budget),
                }
            ).data
        )

    def _reloaded(self, pk: int) -> dict:
        """Re-read through the annotated queryset so counts are in the payload."""
        return self.get_serializer(self.get_queryset().get(pk=pk)).data
