"""
ViewSets for MCP API.
"""

import time

from rest_framework import viewsets, status
from rest_framework import mixins
from rest_framework.decorators import api_view, permission_classes
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from asgiref.sync import async_to_sync

from common.permissions import IsResearcherOrAbove
from mcp.services.mcp_gateway import handle_jsonrpc
from mcp.constants import MCPAuthType
from mcp.models import MCPServer, UserMCPConnection, MCPToolExecution
from mcp.api.serializers import (
    MCPServerCreateSerializer,
    MCPServerSerializer,
    UserMCPConnectionSerializer,
    UserMCPConnectionCreateSerializer,
    MCPToolExecutionSerializer,
    ToolCallSerializer,
    ToolDefinitionSerializer,
    ConnectionTestResultSerializer,
    OAuthStartSerializer,
)
from mcp.services.credential_service import MCPCredentialService
from mcp.services.mcp_manager import mcp_manager, MCPManagerError
from mcp.services.oauth_service import mcp_oauth_service, MCPOAuthError


class MCPServerViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
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

    def get_permissions(self):
        if self.action == 'create':
            return [IsResearcherOrAbove()]
        return [permission() for permission in self.permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return MCPServerCreateSerializer
        return MCPServerSerializer

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

        if not connection or not _connection_has_auth(connection):
            return Response(
                {'error': 'No active connection to this server. Please connect first.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Decrypt credentials
            credentials = _get_connection_credentials(connection)

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

    @action(detail=True, methods=['post'], url_path='oauth/start')
    def oauth_start(self, request, slug=None):
        """Start OAuth for a remote MCP server and return the provider URL."""
        server = self.get_object()
        if server.auth_type != MCPAuthType.OAUTH2:
            return Response(
                {'error': f'{server.name} does not use OAuth.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            oauth_start = mcp_oauth_service.build_authorization_url(
                server=server,
                user_id=request.user.id,
                request=request,
            )
        except MCPOAuthError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OAuthStartSerializer({
            'authorization_url': oauth_start.authorization_url,
            'state': oauth_start.state,
        })
        return Response(serializer.data)


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

        if not _connection_has_auth(connection):
            return Response(
                {'success': False, 'message': 'No credentials configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            credentials = _get_connection_credentials(connection)

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

        if not _connection_has_auth(connection):
            return Response(
                {'error': 'No credentials configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ToolCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tool_name = serializer.validated_data['tool_name']
        arguments = serializer.validated_data['arguments']

        try:
            credentials = _get_connection_credentials(connection)

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


@api_view(['GET'])
@permission_classes([AllowAny])
def oauth_callback(request):
    """Complete remote MCP OAuth and redirect the user back to the MCP page."""
    code = request.query_params.get('code')
    state = request.query_params.get('state')
    error = request.query_params.get('error')

    if error:
        return _oauth_callback_response("", "error", error)
    if not code or not state:
        return _oauth_callback_response("", "error", "Missing OAuth code or state")

    try:
        state_data = mcp_oauth_service.pop_state(state)
        server = MCPServer.active_objects.get(slug=state_data["server_slug"])
        user = get_user_model().objects.get(id=state_data["user_id"])
        token = async_to_sync(mcp_oauth_service.exchange_code)(
            server=server,
            code=code,
            code_verifier=state_data["code_verifier"],
            request=request,
        )

        connection, _ = UserMCPConnection.all_objects.get_or_create(
            user=user,
            server=server,
        )
        connection.encrypted_credentials = MCPCredentialService.encrypt_credentials(
            token.to_credentials()
        )
        connection.auth_metadata = token.to_metadata()
        connection.is_active = True
        connection.is_deleted = False
        connection.save(update_fields=[
            'encrypted_credentials',
            'auth_metadata',
            'is_active',
            'is_deleted',
            'updated_at',
        ])
    except (MCPServer.DoesNotExist, get_user_model().DoesNotExist):
        return _oauth_callback_response("", "error", "OAuth connection target no longer exists")
    except MCPOAuthError as e:
        return _oauth_callback_response("", "error", str(e))
    except Exception as e:
        return _oauth_callback_response("", "error", f"OAuth callback failed: {str(e)}")

    return _oauth_callback_response(server.slug, "success", f"Connected {server.name}")


def _oauth_callback_response(server_slug: str, status_value: str, message: str):
    redirect_url = mcp_oauth_service.get_frontend_redirect_url(
        server_slug=server_slug,
        status=status_value,
        message=message,
    )
    if redirect_url:
        return redirect(redirect_url)
    return Response({
        'server': server_slug,
        'status': status_value,
        'message': message,
    })


def _connection_has_auth(connection: UserMCPConnection) -> bool:
    if connection.server.auth_type == MCPAuthType.NONE:
        return True
    return bool(connection.encrypted_credentials)


def _get_connection_credentials(connection: UserMCPConnection) -> dict:
    credentials = MCPCredentialService.decrypt_credentials(
        connection.encrypted_credentials
    )
    if connection.server.auth_type != MCPAuthType.OAUTH2:
        return credentials

    expires_at = connection.auth_metadata.get("expires_at")
    refresh_token = MCPCredentialService.get_refresh_token(credentials)
    if not expires_at or not refresh_token or expires_at > int(time.time()) + 60:
        return credentials

    token = async_to_sync(mcp_oauth_service.refresh_access_token)(
        connection.server,
        refresh_token,
    )
    connection.encrypted_credentials = MCPCredentialService.encrypt_credentials(
        token.to_credentials()
    )
    connection.auth_metadata = token.to_metadata()
    connection.save(update_fields=[
        'encrypted_credentials',
        'auth_metadata',
        'updated_at',
    ])
    return token.to_credentials()


class MCPGatewayView(APIView):
    """
    MCP Streamable HTTP gateway — exposes the authenticated user's connected MCP
    tools to an external agent (Hermes). Plain JSON in/out (no camelCase) to keep
    the JSON-RPC envelope and tool arguments/results verbatim. Credentials and
    audit stay in DARE (calls route through the executor).

    POST /mcp/api/gateway/  — one MCP JSON-RPC message.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        # The agent runtime forwards its run/session id here (DARE's
        # "<hermes_session_id>-r<run_id>") when configured to, so captured rows
        # can be attributed to the exact run. Absent until Hermes forwards it;
        # the audit falls back to in-order matching in that case.
        run_key = request.headers.get("X-DARE-Run-Session", "")
        response = handle_jsonrpc(request.user, payload, run_key)
        if response is None:  # notification — no body
            return HttpResponse(status=202)
        return Response(response)
