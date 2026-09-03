"""Panel briefs: the defaults each role runs under, and the person's saved presets."""

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.permissions import IsOwner
from conversations.api.serializers import EnsemblePresetSerializer
from conversations.models import EnsemblePreset
from core.services.dtos.ensemble_dto import BRIEF_ROLES
from workflows.services.ensemble_workflow_builder import role_prompt_content


class EnsemblePresetViewSet(viewsets.ModelViewSet):
    """Saved briefs for panel and council turns, private to their owner."""

    serializer_class = EnsemblePresetSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return EnsemblePreset.active_objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=False, methods=["get"])
    def defaults(self, request):
        """What each role is told when no brief overrides it."""
        return Response(
            {role: role_prompt_content(request.user, role) for role in BRIEF_ROLES}
        )
