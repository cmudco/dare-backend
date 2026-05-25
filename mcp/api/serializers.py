"""
Serializers for MCP API.
"""

from rest_framework import serializers
from mcp.constants import MCPAuthType, MCPTransport
from mcp.models import MCPServer, UserMCPConnection, MCPToolExecution
from mcp.services.credential_service import MCPCredentialService


class MCPServerSerializer(serializers.ModelSerializer):
    """Serializer for listing available MCP servers."""

    class Meta:
        model = MCPServer
        fields = [
            'id',
            'name',
            'slug',
            'description',
            'icon',
            'transport',
            'auth_type',
            'remote_url',
            'required_credentials',
            'credentials_help_url',
            'setup_guide',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields


class MCPServerCreateSerializer(serializers.ModelSerializer):
    """Staff-only serializer for adding hosted remote MCP servers."""

    class Meta:
        model = MCPServer
        fields = [
            'id',
            'name',
            'slug',
            'description',
            'icon',
            'transport',
            'auth_type',
            'remote_url',
            'remote_headers',
            'oauth_authorize_url',
            'oauth_token_url',
            'oauth_registration_url',
            'oauth_scope',
            'oauth_client_id',
            'required_credentials',
            'credentials_help_url',
            'setup_guide',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        transport = attrs.get('transport', MCPTransport.STREAMABLE_HTTP)
        auth_type = attrs.get('auth_type', MCPAuthType.NONE)
        remote_url = attrs.get('remote_url', '')

        if transport != MCPTransport.STREAMABLE_HTTP:
            raise serializers.ValidationError({
                'transport': 'In-app MCP server creation only supports hosted Streamable HTTP MCP servers.'
            })

        if not remote_url:
            raise serializers.ValidationError({
                'remote_url': 'Remote MCP URL is required.'
            })

        if not remote_url.startswith('https://'):
            raise serializers.ValidationError({
                'remote_url': 'Remote MCP URL must use HTTPS.'
            })

        if auth_type == MCPAuthType.OAUTH2:
            registration_url = attrs.get('oauth_registration_url', '')
            client_id = attrs.get('oauth_client_id', '')
            authorize_url = attrs.get('oauth_authorize_url', '')
            token_url = attrs.get('oauth_token_url', '')

            if not registration_url and not client_id:
                raise serializers.ValidationError({
                    'oauth_registration_url': 'OAuth MCP servers need dynamic registration or a client ID.'
                })
            if client_id and (not authorize_url or not token_url):
                raise serializers.ValidationError({
                    'oauth_authorize_url': 'Static OAuth client configuration requires authorize and token URLs.'
                })

        return attrs

    def create(self, validated_data):
        validated_data['transport'] = MCPTransport.STREAMABLE_HTTP
        validated_data.setdefault('command', '')
        validated_data.setdefault('args', [])
        validated_data.setdefault('docker_image', '')
        validated_data.setdefault('remote_headers', {})
        validated_data.setdefault('required_credentials', [])
        return super().create(validated_data)


class UserMCPConnectionSerializer(serializers.ModelSerializer):
    """
    Serializer for user MCP connections.
    
    Returns masked credentials for security.
    """
    server = MCPServerSerializer(read_only=True)
    server_slug = serializers.SlugRelatedField(
        queryset=MCPServer.active_objects.all(),
        slug_field='slug',
        write_only=True,
        source='server'
    )
    masked_credentials = serializers.SerializerMethodField()
    has_credentials = serializers.SerializerMethodField()

    class Meta:
        model = UserMCPConnection
        fields = [
            'id',
            'server',
            'server_slug',
            'masked_credentials',
            'has_credentials',
            'auth_metadata',
            'is_active',
            'last_used_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'server', 'masked_credentials', 'has_credentials',
            'auth_metadata', 'last_used_at', 'created_at', 'updated_at'
        ]

    def get_masked_credentials(self, obj):
        """Return masked version of stored credentials."""
        if not obj.encrypted_credentials:
            return {}
        
        # Decrypt then mask
        decrypted = MCPCredentialService.decrypt_credentials(obj.encrypted_credentials)
        return MCPCredentialService.mask_credentials(decrypted)

    def get_has_credentials(self, obj):
        """Check if credentials are set."""
        if obj.server.auth_type == MCPAuthType.NONE:
            return True
        return bool(obj.encrypted_credentials)


class UserMCPConnectionCreateSerializer(serializers.Serializer):
    """
    Serializer for creating/updating MCP connections with credentials.
    """
    server_slug = serializers.SlugRelatedField(
        queryset=MCPServer.active_objects.all(),
        slug_field='slug',
        source='server'
    )
    credentials = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        help_text="Credential key-value pairs"
    )

    def validate(self, attrs):
        """Validate required credentials are provided."""
        server = attrs['server']
        credentials = attrs['credentials']

        if server.auth_type == MCPAuthType.NONE:
            return attrs

        if server.auth_type == MCPAuthType.OAUTH2:
            raise serializers.ValidationError({
                'credentials': 'OAuth MCP servers must be connected through the OAuth flow.'
            })

        is_valid, missing = MCPCredentialService.validate_credentials_schema(
            credentials,
            server.required_credentials
        )

        if not is_valid:
            raise serializers.ValidationError({
                'credentials': f"Missing required credentials: {', '.join(missing)}"
            })

        return attrs

    def create(self, validated_data):
        """Create or update connection with encrypted credentials."""
        user = self.context['request'].user
        server = validated_data['server']
        credentials = validated_data['credentials']

        # Encrypt credentials
        encrypted = MCPCredentialService.encrypt_credentials(credentials)

        # Get or create connection
        connection, created = UserMCPConnection.all_objects.get_or_create(
            user=user,
            server=server,
            defaults={
                'encrypted_credentials': encrypted,
                'auth_metadata': {
                    "auth_type": server.auth_type,
                },
            }
        )

        if not created:
            connection.encrypted_credentials = encrypted
            connection.auth_metadata = {
                "auth_type": server.auth_type,
            }
            connection.is_active = True
            connection.is_deleted = False
            connection.save(update_fields=[
                'encrypted_credentials',
                'auth_metadata',
                'is_active',
                'is_deleted',
                'updated_at',
            ])

        return connection


class OAuthStartSerializer(serializers.Serializer):
    """OAuth authorization URL returned for remote MCP login."""

    authorization_url = serializers.URLField()
    state = serializers.CharField()


class MCPToolExecutionSerializer(serializers.ModelSerializer):
    """Serializer for tool execution history."""

    server_name = serializers.CharField(source='server.name', read_only=True)
    server_slug = serializers.CharField(source='server.slug', read_only=True)

    class Meta:
        model = MCPToolExecution
        fields = [
            'id',
            'server_name',
            'server_slug',
            'tool_name',
            'tool_arguments',
            'status',
            'result',
            'error_message',
            'execution_time_ms',
            'created_at',
        ]
        read_only_fields = fields


class ToolCallSerializer(serializers.Serializer):
    """Serializer for executing a tool call."""

    tool_name = serializers.CharField(max_length=255)
    arguments = serializers.DictField(default=dict)


class ToolDefinitionSerializer(serializers.Serializer):
    """Serializer for tool definitions returned by MCP servers."""

    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True, required=False)
    inputSchema = serializers.DictField(required=False)


class ConnectionTestResultSerializer(serializers.Serializer):
    """Serializer for connection test results."""

    success = serializers.BooleanField()
    message = serializers.CharField()
