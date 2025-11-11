from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from django.db.models import Max

from common.permissions import IsOwner
from agents.models import Agent
from .serializers import AgentSerializer, AgentListSerializer


class AgentViewSet(viewsets.ModelViewSet):
    """
    Endpoint for listing, retrieving, creating, updating and deleting agents.

    Agents are reusable configurations for workflow nodes similar to prompts.
    They encapsulate an LLM configuration with prompt, files, and model settings.
    """
    serializer_class = AgentSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        """Return agents owned by the current user."""
        return Agent.active_objects.filter(user=self.request.user).order_by('-created_at')

    def get_serializer_class(self):
        """Return lightweight serializer for list action."""
        if self.action == 'list':
            return AgentListSerializer
        return AgentSerializer

    def perform_destroy(self, instance):
        """Override delete to recursively delete all children agents."""
        children_to_delete = []

        def collect_children(agent):
            children = Agent.active_objects.filter(parent=agent)
            for child in children:
                children_to_delete.append(child)
                collect_children(child)

        collect_children(instance)
        instance.delete()

        for child in children_to_delete:
            child.delete()

    def create(self, request, *args, **kwargs):
        """Create a new agent."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """
        Update an agent by creating a new version.

        Only the latest version of an agent can be updated.
        Updating an agent creates a new version with the old agent as parent.
        """
        instance = self.get_object()

        # Find root agent
        root_agent = instance
        while root_agent.parent is not None:
            root_agent = root_agent.parent

        # Collect all family agents
        family_agents = []
        def collect_family(agent):
            family_agents.append(agent)
            children = Agent.active_objects.filter(parent=agent)
            for child in children:
                collect_family(child)

        collect_family(root_agent)

        # Check if this is the latest version
        latest_version = max([a.version for a in family_agents], default=0)

        if instance.version != latest_version:
            return Response(
                {"detail": "Only the latest version can be updated."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create new version
        new_version = instance.version + 1
        new_agent = Agent(
            user=instance.user,
            name=request.data.get('name', instance.name),
            description=request.data.get('description', instance.description),
            prompt_id=request.data.get('prompt', instance.prompt_id),
            llm_id=request.data.get('llm', instance.llm_id),
            max_tokens=request.data.get('max_tokens', instance.max_tokens),
            temperature=request.data.get('temperature', instance.temperature),
            max_context_snippets=request.data.get('max_context_snippets', instance.max_context_snippets),
            document_similarity_threshold=request.data.get('document_similarity_threshold', instance.document_similarity_threshold),
            enable_web_search=request.data.get('enable_web_search', instance.enable_web_search),
            version=new_version,
            parent=instance
        )
        new_agent.save()

        # Handle M2M relationships
        if 'content_files' in request.data:
            new_agent.content_files.set(request.data.get('content_files', []))
        else:
            new_agent.content_files.set(instance.content_files.all())

        if 'embedding_files' in request.data:
            new_agent.embedding_files.set(request.data.get('embedding_files', []))
        else:
            new_agent.embedding_files.set(instance.embedding_files.all())

        serializer = self.get_serializer(new_agent)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def clone_agent(self, request, pk=None):
        """
        Custom action to clone an agent.

        Cloning creates a new agent with the same configuration but no parent.
        """
        instance = self.get_object()

        cloned_agent = Agent(
            user=instance.user,
            name=f"COPY OF - {instance.name}",
            description=instance.description,
            prompt=instance.prompt,
            llm=instance.llm,
            max_tokens=instance.max_tokens,
            temperature=instance.temperature,
            max_context_snippets=instance.max_context_snippets,
            document_similarity_threshold=instance.document_similarity_threshold,
            enable_web_search=instance.enable_web_search,
            version=1,
            parent=None
        )
        cloned_agent.save()

        # Copy M2M relationships
        cloned_agent.content_files.set(instance.content_files.all())
        cloned_agent.embedding_files.set(instance.embedding_files.all())

        serializer = self.get_serializer(cloned_agent)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def simple_update(self, request, pk=None):
        """
        Custom action to perform a simple update without creating a new version.

        This is useful for quick edits without the versioning overhead.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Search agents by name or description.

        Query parameters:
        - q: Search term (searches in name and description)
        """
        query = request.query_params.get('q', '')
        agents = self.get_queryset()

        if query:
            agents = agents.filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )

        serializer = AgentListSerializer(agents, many=True)
        return Response(serializer.data)
