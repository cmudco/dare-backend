from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from common.permissions import IsOwner
from prompts.models import Prompt
from .serializers import PromptSerializer

class PromptViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting prompts."""
    serializer_class = PromptSerializer
    permission_classes = [IsAuthenticated,IsOwner]

    def get_queryset(self):
        return Prompt.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)