"""
ViewSets for MCP API.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.utils import timezone
from asgiref.sync import async_to_sync

from mcp.models import MCPServer, UserMCPConnection, MCPToolExecution
from mcp.api.serializers import (
    MCPServerSerializer,
    UserMCPConnectionSerializer,
    UserMCPConnectionCreateSerializer,
    MCPToolExecutionSerializer,
    ToolCallSerializer,
    ToolDefinitionSerializer,
    ConnectionTestResultSerializer,
)
from mcp.services.credential_service import MCPCredentialService
from mcp.services.mcp_manager import mcp_manager, MCPManagerError


class MCPServerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing available MCP servers.
    
    Endpoints:
    - GET /mcp/api/servers/ - List all active servers
    - GET /mcp/api/servers/{slug}/ - Get server details
    - GET /mcp/api/servers/{slug}/tools/ - Get tools for a server (requires connection)
    """
    serializer_class = MCPServerSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'slug'

    def get_queryset(self):
        return MCPServer.active_objects.all()

    @action(detail=True, methods=['get'], url_path='tools')
    def tools(self, request, slug=None):
        """
        Get available tools for an MCP server.
        
        Requires the user to have an active connection to this server.
        Tools are cached in Redis to avoid repeated subprocess spawns.
        """
        server = self.get_object()

        # Check user has a connection
        connection = UserMCPConnection.active_objects.filter(
            user=request.user,
            server=server
        ).first()

        if not connection or not connection.encrypted_credentials:
            return Response(
                {'error': 'No active connection to this server. Please connect first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Decrypt credentials
            credentials = MCPCredentialService.decrypt_credentials(
                connection.encrypted_credentials
            )

            # Get tools (cached in Redis)
            tools = async_to_sync(mcp_manager.get_available_tools)(server, credentials)

            # Also update DB cache as fallback
            connection.cached_tools = tools

            connection.tools_cached_at = timezone.now()
            connection.save(update_fields=['cached_tools', 'tools_cached_at'])

            serializer = ToolDefinitionSerializer(tools, many=True)
            return Response({
                'tools': serializer.data,
                'count': len(tools),
                'cached': True  # Could be enhanced to indicate cache hit/miss
            })

        except MCPManagerError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            return Response(
                {'error': f'Failed to get tools: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserMCPConnectionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user's MCP connections.
    
    Endpoints:
    - GET /mcp/api/connections/ - List user's connections
    - POST /mcp/api/connections/ - Create/update connection with credentials
    - GET /mcp/api/connections/{server_slug}/ - Get connection details
    - DELETE /mcp/api/connections/{server_slug}/ - Disconnect from server
    - POST /mcp/api/connections/{server_slug}/test/ - Test connection
    - POST /mcp/api/connections/{server_slug}/execute/ - Execute a tool
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'server__slug'
    lookup_url_kwarg = 'server_slug'

    def get_queryset(self):
        return UserMCPConnection.active_objects.filter(
            user=self.request.user
        ).select_related('server')

    def get_serializer_class(self):
        if self.action == 'create':
            return UserMCPConnectionCreateSerializer
        return UserMCPConnectionSerializer

    def create(self, request, *args, **kwargs):
        """Create or update an MCP connection with credentials."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = serializer.save()

        response_serializer = UserMCPConnectionSerializer(connection)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """Disconnect from an MCP server (soft delete)."""
        connection = self.get_object()
        connection.is_active = False
        connection.encrypted_credentials = {}
        connection.save(update_fields=['is_active', 'encrypted_credentials', 'updated_at'])

        return Response(
            {'message': f'Disconnected from {connection.server.name}'},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], url_path='test')
    def test_connection(self, request, server_slug=None):
        """Test that the connection credentials work."""
        connection = self.get_object()

        if not connection.encrypted_credentials:
            return Response(
                {'success': False, 'message': 'No credentials configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            credentials = MCPCredentialService.decrypt_credentials(
                connection.encrypted_credentials
            )

            success, message = async_to_sync(mcp_manager.test_connection)(
                connection.server,
                credentials
            )

            serializer = ConnectionTestResultSerializer({
                'success': success,
                'message': message
            })
            return Response(serializer.data)

        except Exception as e:
            return Response({
                'success': False,
                'message': f'Test failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='execute')
    def execute_tool(self, request, server_slug=None):
        """
        Execute a tool on this MCP server.
        
        Request body:
        {
            "tool_name": "send_message",
            "arguments": {"channel": "C123", "text": "Hello"}
        }
        """
        connection = self.get_object()

        if not connection.encrypted_credentials:
            return Response(
                {'error': 'No credentials configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ToolCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tool_name = serializer.validated_data['tool_name']
        arguments = serializer.validated_data['arguments']

        try:
            credentials = MCPCredentialService.decrypt_credentials(
                connection.encrypted_credentials
            )

            result = async_to_sync(mcp_manager.call_tool)(
                user=request.user,
                server=connection.server,
                tool_name=tool_name,
                arguments=arguments,
                credentials=credentials
            )

            return Response({
                'success': True,
                'result': result
            })

        except MCPManagerError as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Execution failed: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MCPToolExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for tool execution history.
    
    Endpoints:
    - GET /mcp/api/executions/ - List user's tool executions
    - GET /mcp/api/executions/{id}/ - Get execution details
    """
    serializer_class = MCPToolExecutionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = MCPToolExecution.active_objects.filter(
            user=self.request.user
        ).select_related('server')

        # Optional filtering
        server_slug = self.request.query_params.get('server')
        if server_slug:
            queryset = queryset.filter(server__slug=server_slug)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset
