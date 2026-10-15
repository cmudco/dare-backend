from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from agents.models import Agent
from agents.services.agent_service import clone_agent, save_new_version
from common.permissions import IsOwner

from .serializers import AgentSerializer


class AgentViewSet(viewsets.ModelViewSet):
    """
    Endpoint for listing, retrieving, creating, updating and deleting agents.

    An agent is a reusable chat setup: prompt, model (DARE or LiteLLM gateway),
    settings, sources and tools.
    """

    serializer_class = AgentSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return (
            Agent.active_objects.filter(user=self.request.user)
            .select_related("user", "prompt", "llm")
            .prefetch_related(
                "content_files",
                "embedding_files",
                "libraries",
                "mcp_servers",
                "dare_tools",
            )
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        """Delete the agent and every later version derived from it."""
        children_to_delete = []

        def collect_children(agent):
            for child in Agent.active_objects.filter(parent=agent):
                children_to_delete.append(child)
                collect_children(child)

        collect_children(instance)
        instance.delete()
        for child in children_to_delete:
            child.delete()

    def update(self, request, *args, **kwargs):
        """Save the edit as a new version; only the latest version can be edited."""
        instance = self.get_object()
        if Agent.active_objects.filter(parent=instance).exists():
            return Response(
                {"detail": "Only the latest version can be updated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_agent = save_new_version(instance, serializer.validated_data)
        return Response(self.get_serializer(new_agent).data)

    @action(detail=True, methods=["post"])
    def clone_agent(self, request, pk=None):
        cloned = clone_agent(self.get_object())
        return Response(
            self.get_serializer(cloned).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def simple_update(self, request, pk=None):
        """Edit the agent in place, without creating a new version."""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def search(self, request):
        """Agents whose name or description contains `q`."""
        query = request.query_params.get("q", "")
        agents = self.get_queryset()
        if query:
            agents = agents.filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )
        return Response(self.get_serializer(agents, many=True).data)
