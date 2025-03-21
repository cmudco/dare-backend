from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common.permissions import IsOwner
from workflows.api.serializers import WorkflowSerializer, StepSerializer
from workflows.models import Workflow, Step


class WorkflowViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting workflows."""
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Workflow.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class StepViewSet(viewsets.ModelViewSet):
    """Endpoint for managing workflow steps."""
    serializer_class = StepSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Step.objects.filter(user=self.request.user).order_by('order')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)