"""
Serializers for MCP API.
"""

from rest_framework import serializers
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
            'required_credentials',
            'credentials_help_url',
            'setup_guide',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields


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
            'is_active',
            'last_used_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'server', 'masked_credentials', 'has_credentials',
            'last_used_at', 'created_at', 'updated_at'
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
            defaults={'encrypted_credentials': encrypted}
        )

        if not created:
            connection.encrypted_credentials = encrypted
            connection.is_active = True
            connection.is_deleted = False
            connection.save(update_fields=['encrypted_credentials', 'is_active', 'is_deleted', 'updated_at'])

        return connection


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
