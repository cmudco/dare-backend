from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.permissions import IsOwner
from prompts.models import Prompt, PublishedPrompt
from sharing.services.sharing_service import SharingService
from .serializers import (
    PromptSerializer,
    PublishedPromptSerializer,
    PublishPromptSerializer,
)


def clone_prompt_to_user(source_prompt: Prompt, user) -> Prompt:
    """Create a user-owned copy of a prompt."""
    cloned_prompt = Prompt(
        user=user,
        title=f"COPY OF - {source_prompt.title}",
        content=source_prompt.content,
        version=1,
        parent=None,
    )
    cloned_prompt.save()
    return cloned_prompt


class PromptViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting prompts."""
    serializer_class = PromptSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Prompt.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_destroy(self, instance):
        """Override delete to recursively delete all parent prompts."""
        parents_to_delete = []
        current_parent = instance.parent

        while current_parent is not None:
            parents_to_delete.append(current_parent)
            current_parent = current_parent.parent

        instance.delete()

        for parent in parents_to_delete:
            parent.delete()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prompt = serializer.save(user=request.user)
        if request.data.get('is_default', False):
            request.user.default_prompt = prompt
            request.user.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()

        root_prompt = instance
        while root_prompt.parent is not None:
            root_prompt = root_prompt.parent

        family_prompts = []
        def collect_family(prompt):
            family_prompts.append(prompt)
            children = Prompt.active_objects.filter(parent=prompt)
            for child in children:
                collect_family(child)

        collect_family(root_prompt)

        latest_version = max([p.version for p in family_prompts], default=0)

        if instance.version != latest_version:
            return Response(
                {"detail": "Only the latest version can be updated."},
                status=status.HTTP_400_BAD_REQUEST
            )

        new_version = instance.version + 1
        new_prompt = Prompt(
            user=instance.user,
            title=request.data.get('title', instance.title),
            content=request.data.get('content', instance.content),
            version=new_version,
            parent=instance
        )
        new_prompt.save()

        if request.data.get('is_default', False):
            request.user.default_prompt = new_prompt
            request.user.save()

        serializer = self.get_serializer(new_prompt)
        return Response(serializer.data)

    def clone_prompt(self, request, pk=None):
        """Custom action to clone a prompt."""
        instance = Prompt.active_objects.filter(pk=pk, user=request.user).first()
        if not instance:
            instance = Prompt.active_objects.filter(pk=pk).first()

        if not instance:
            return Response(
                {"detail": "Prompt not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if instance.user != request.user and not SharingService.can_access(
            request.user,
            "prompt",
            instance.pk,
        ):
            return Response(
                {"detail": "You do not have permission to clone this prompt."},
                status=status.HTTP_403_FORBIDDEN,
            )

        cloned_prompt = clone_prompt_to_user(instance, request.user)

        serializer = self.get_serializer(cloned_prompt)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def simple_update(self, request, pk=None):
        """Custom action to perform a simple update without creating a new version."""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if request.data.get('is_default', False):
            request.user.default_prompt = instance
            request.user.save()
        return Response(serializer.data)

    def publish_prompt(self, request, pk=None):
        """Publish a prompt to the public library."""
        instance = self.get_object()
        
        # Check if already published
        if hasattr(instance, 'published') and instance.published:
            return Response(
                {"detail": "Prompt is already published."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate input
        input_serializer = PublishPromptSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        
        # Create published record
        published = PublishedPrompt.objects.create(
            prompt=instance,
            description=input_serializer.validated_data.get('description', '')
        )
        
        serializer = PublishedPromptSerializer(published)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def unpublish_prompt(self, request, pk=None):
        """Remove a prompt from the public library."""
        instance = self.get_object()
        
        if not hasattr(instance, 'published') or not instance.published:
            return Response(
                {"detail": "Prompt is not published."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        instance.published.delete()
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
        cloned = clone_prompt_to_user(published.prompt, request.user)
        
        return Response(
            PromptSerializer(cloned).data,
            status=status.HTTP_201_CREATED
        )
