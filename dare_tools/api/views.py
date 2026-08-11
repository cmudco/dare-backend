"""
DARE Tools API views.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from dare_tools.models import DareTool, DareToolExecution
from dare_tools.api.serializers import DareToolSerializer, DareToolExecutionSerializer


class DareToolViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing and retrieving DARE tools.
    
    list: Get all active DARE tools
    retrieve: Get a specific DARE tool by ID
    """
    serializer_class = DareToolSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'slug'
    
    def get_queryset(self):
        return DareTool.active_objects.filter(is_active=True)
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """Get tools grouped by category."""
        tools = self.get_queryset()
        categories = {}
        
        for tool in tools:
            if tool.category not in categories:
                categories[tool.category] = []
            categories[tool.category].append(DareToolSerializer(tool).data)
        
        return Response(categories)


class DareToolExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing tool execution history.
    
    list: Get user's tool execution history
    retrieve: Get a specific execution by ID
    """
    serializer_class = DareToolExecutionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return DareToolExecution.active_objects.filter(
            user=self.request.user
        ).select_related('tool').order_by('-created_at')
