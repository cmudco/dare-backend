"""
DARE Tools admin configuration.
"""

from django.contrib import admin
from dare_tools.models import DareTool, DareToolExecution


@admin.register(DareTool)
class DareToolAdmin(admin.ModelAdmin):
    """Admin for DARE Tool definitions."""
    list_display = ['name', 'slug', 'category', 'function_name', 'is_active', 'created_at']
    list_filter = ['category', 'is_active', 'is_deleted']
    search_fields = ['name', 'slug', 'description', 'function_name']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['category', 'name']


@admin.register(DareToolExecution)
class DareToolExecutionAdmin(admin.ModelAdmin):
    """Admin for DARE Tool execution history."""
    list_display = ['tool', 'user', 'status', 'execution_time_ms', 'created_at']
    list_filter = ['status', 'tool', 'created_at']
    search_fields = ['user__email', 'tool__name', 'tool_call_id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    
    raw_id_fields = ['user', 'tool', 'message', 'conversation']
