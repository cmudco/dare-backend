"""
DARE Tools models.

Models:
- DareTool: Internal DARE tool definition (seeded by admin/fixtures)
- DareToolExecution: Audit trail for tool executions
"""

from django.db import models
from django.conf import settings
from common.models import BaseModel
from common.managers import ActiveObjectsManager
from dare_tools.constants import ToolCategory, ExecutionStatus


class DareTool(BaseModel):
    """
    Internal DARE tool definition.
    
    Unlike MCP servers, these tools require no credentials and execute
    directly in Python. Examples: diagram generation, chart creation.
    """
    name = models.CharField(
        max_length=100,
        help_text="Display name for this tool (e.g., 'Create Diagram')"
    )
    slug = models.SlugField(
        unique=True,
        help_text="URL-friendly identifier (e.g., 'create_diagram')"
    )
    description = models.TextField(
        blank=True,
        help_text="Description of what this tool does"
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon identifier for frontend (e.g., 'diagram', 'chart')"
    )
    category = models.CharField(
        max_length=50,
        choices=ToolCategory.choices(),
        default=ToolCategory.VISUALIZATION,
        help_text="Tool category for UI grouping"
    )

    # The function name used by LLM (maps to registry)
    function_name = models.CharField(
        max_length=100,
        help_text="The function name the LLM will call (e.g., 'create_diagram')"
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "DARE Tool"
        verbose_name_plural = "DARE Tools"
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.name} ({self.slug})"


class DareToolExecution(BaseModel):
    """
    Records each DARE tool execution for audit/history.
    
    Links to Message/Conversation when called from LLM context.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dare_tool_executions',
        null=True,
        blank=True,
        help_text="User who executed this tool (null for public bots)"
    )
    tool = models.ForeignKey(
        DareTool,
        on_delete=models.CASCADE,
        related_name='executions',
        help_text="DARE tool that was executed"
    )

    # Optional links to conversation context
    message = models.ForeignKey(
        'conversations.Message',
        on_delete=models.CASCADE,
        related_name='dare_tool_executions',
        null=True,
        blank=True,
        help_text="Message that triggered this tool call (if from LLM)"
    )
    conversation = models.ForeignKey(
        'conversations.Conversation',
        on_delete=models.CASCADE,
        related_name='dare_tool_executions',
        null=True,
        blank=True,
        help_text="Conversation context (if from LLM)"
    )

    # Tool call details
    tool_call_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Unique ID from LLM tool call"
    )
    arguments = models.JSONField(
        default=dict,
        help_text="Arguments passed to the tool"
    )

    # Execution result
    status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices(),
        default=ExecutionStatus.PENDING,
        help_text="Execution status"
    )
    result = models.JSONField(
        null=True,
        blank=True,
        help_text="Tool response/result"
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error message if execution failed"
    )

    # Performance tracking
    execution_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Execution time in milliseconds"
    )

    all_objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "DARE Tool Execution"
        verbose_name_plural = "DARE Tool Executions"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'tool']),
            models.Index(fields=['status']),
            models.Index(fields=['tool_call_id']),
        ]

    def __str__(self):
        tool_name = self.tool.name if self.tool else "Unknown"
        user_email = self.user.email if self.user else "Anonymous"
        return f"{tool_name} ({self.status}) - {user_email}"
