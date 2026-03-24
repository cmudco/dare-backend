from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.permissions import IsOwner
from prompts.models import Prompt, PublishedPrompt
from prompts.services import (
    PromptCloningService,
    PromptService,
    PromptServiceError,
)
from .serializers import (
    PromptSerializer,
    PublishedPromptSerializer,
    PublishPromptSerializer,
)


class PromptViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting prompts."""
    serializer_class = PromptSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    @staticmethod
    def _service_error_response(error: PromptServiceError) -> Response:
        """Convert a prompt service error into an HTTP response."""
        return Response(
            {"detail": str(error)},
            status=error.status_code,
        )

    def get_queryset(self):
        return Prompt.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_destroy(self, instance):
        """Override delete to recursively delete all parent prompts."""
        PromptService.delete_prompt_family(instance)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prompt = PromptService.create_prompt(
            validated_data=serializer.validated_data,
            user=request.user,
            is_default=request.data.get('is_default', False),
        )
        response_serializer = self.get_serializer(prompt)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            new_prompt = PromptService.create_next_version(
                prompt=instance,
                validated_data=serializer.validated_data,
                user=request.user,
                is_default=request.data.get('is_default', False),
            )
        except PromptServiceError as error:
            return self._service_error_response(error)

        response_serializer = self.get_serializer(new_prompt)
        return Response(response_serializer.data)

    def clone_prompt(self, request, pk=None):
        """Custom action to clone a prompt."""
        try:
            source_prompt = PromptService.get_cloneable_prompt(int(pk), request.user)
        except PromptServiceError as error:
            return self._service_error_response(error)

        cloned_prompt = PromptCloningService.clone_to_user(source_prompt, request.user)
        response_serializer = self.get_serializer(cloned_prompt)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def simple_update(self, request, pk=None):
        """Custom action to perform a simple update without creating a new version."""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        PromptService.set_default_prompt(
            user=request.user,
            prompt=instance,
            is_default=request.data.get('is_default', False),
        )
        return Response(serializer.data)

    def publish_prompt(self, request, pk=None):
        """Publish a prompt to the public library."""
        instance = self.get_object()

        input_serializer = PublishPromptSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            published = PromptService.publish_prompt(
                prompt=instance,
                description=input_serializer.validated_data.get('description', ''),
            )
        except PromptServiceError as error:
            return self._service_error_response(error)

        response_serializer = PublishedPromptSerializer(published)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def unpublish_prompt(self, request, pk=None):
        """Remove a prompt from the public library."""
        instance = self.get_object()

        try:
            PromptService.unpublish_prompt(instance)
        except PromptServiceError as error:
            return self._service_error_response(error)

        return Response(status=status.HTTP_204_NO_CONTENT)


class PublishedPromptViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Public library of published prompts.
    
    Provides read-only access to all published prompts and a clone action
    to copy a published prompt to the current user's prompts.
    """
    serializer_class = PublishedPromptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PublishedPrompt.active_objects.select_related('prompt', 'prompt__user').all()

    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        """Clone a published prompt to user's prompts."""
        published = self.get_object()
        cloned = PromptCloningService.clone_to_user(published.prompt, request.user)
        
        return Response(
            PromptSerializer(cloned).data,
            status=status.HTTP_201_CREATED
        )
