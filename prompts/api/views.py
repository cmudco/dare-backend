from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from common.permissions import IsOwner
from prompts.models import Prompt
from .serializers import PromptSerializer
from django.db.models import Max

class PromptViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting prompts."""
    serializer_class = PromptSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Prompt.active_objects.filter(user=self.request.user).order_by('-created_at')

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
        instance = self.get_object()
        
        cloned_prompt = Prompt(
            user=instance.user,
            title=f"COPY OF - {instance.title}",
            content=instance.content,
            version=1,
            parent=None
        )
        cloned_prompt.save()

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