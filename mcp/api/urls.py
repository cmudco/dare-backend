"""
URL routing for MCP API.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from mcp.api.views import (
    MCPServerViewSet,
    UserMCPConnectionViewSet,
    MCPToolExecutionViewSet,
    MCPGatewayView,
    QuillmarkQuillsView,
    oauth_callback,
)

router = DefaultRouter()
router.register('servers', MCPServerViewSet, basename='mcp-servers')
router.register('connections', UserMCPConnectionViewSet, basename='mcp-connections')
router.register('executions', MCPToolExecutionViewSet, basename='mcp-executions')

urlpatterns = [
    path('oauth/callback/', oauth_callback, name='oauth-callback'),
    path('gateway/', MCPGatewayView.as_view(), name='mcp-gateway'),
    path('quillmark/quills/', QuillmarkQuillsView.as_view(), name='quillmark-quills'),
    path('', include(router.urls)),
]
