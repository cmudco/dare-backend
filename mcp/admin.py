"""
Admin configuration for MCP models.
"""

from django.contrib import admin
from mcp.models import MCPServer, UserMCPConnection, MCPToolExecution


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    """Admin interface for managing MCP servers."""

    list_display = ['name', 'slug', 'command', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'slug', 'description']
    readonly_fields = ['created_at', 'updated_at']
    prepopulated_fields = {'slug': ('name',)}

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'description', 'icon')
        }),
        ('Runtime Configuration', {
            'fields': ('command', 'args'),
            'description': 'Command and arguments to spawn the MCP server subprocess.'
        }),
        ('Credentials Schema', {
            'fields': ('required_credentials',),
            'description': 'Define what credentials users need to provide to connect.'
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(UserMCPConnection)
class UserMCPConnectionAdmin(admin.ModelAdmin):
    """Admin interface for viewing user MCP connections."""

    list_display = ['user', 'server', 'is_active', 'last_used_at', 'created_at']
    list_filter = ['server', 'is_active', 'created_at']
    search_fields = ['user__email', 'server__name']
    readonly_fields = ['created_at', 'updated_at', 'last_used_at', 'tools_cached_at']
    raw_id_fields = ['user']

    fieldsets = (
        ('Connection', {
            'fields': ('user', 'server')
        }),
        ('Credentials', {
            'fields': ('encrypted_credentials',),
            'description': '⚠️ SECURITY: Credentials are encrypted. Values shown are ciphertext.'
        }),
        ('Cache', {
            'fields': ('cached_tools', 'tools_cached_at'),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('is_active', 'last_used_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        # Connections should be created via API, not admin
        return False


@admin.register(MCPToolExecution)
class MCPToolExecutionAdmin(admin.ModelAdmin):
    """Admin interface for viewing MCP tool execution history."""

    list_display = ['tool_name', 'server', 'user', 'status', 'execution_time_ms', 'created_at']
    list_filter = ['status', 'server', 'created_at']
    search_fields = ['tool_name', 'user__email', 'server__name']
    readonly_fields = [
        'user', 'server', 'message', 'conversation',
        'tool_name', 'tool_arguments', 'status', 'result',
        'error_message', 'execution_time_ms', 'created_at', 'updated_at'
    ]
    raw_id_fields = ['user', 'message', 'conversation']

    fieldsets = (
        ('Context', {
            'fields': ('user', 'server', 'message', 'conversation')
        }),
        ('Tool Call', {
            'fields': ('tool_name', 'tool_arguments')
        }),
        ('Result', {
            'fields': ('status', 'result', 'error_message', 'execution_time_ms')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        # Executions are created automatically, not manually
        return False

    def has_change_permission(self, request, obj=None):
        # Execution records should be immutable
        return False
